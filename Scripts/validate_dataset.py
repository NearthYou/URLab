from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

if __package__:
    from .simtrace_plots import bot_frame_times_by_capture, write_plots
else:
    from simtrace_plots import bot_frame_times_by_capture, write_plots


POSITION_TARGETS_CM = {
    "mean": 10.0,
    "p95": 25.0,
    "final": 50.0,
    "start": 0.1,
}

VALID_MODES = {"human", "bot", "input-replay"}
VALID_END_REASONS = {
    "goal",
    "timeout",
    "fell",
    "manual_abort",
    "capture_error",
    "io_error",
    "replay_source_end",
}
SUPPORTED_SCHEMA_VERSIONS = {1, 2}
COMBAT_CONTRACT = "one_bullet_outcome_ledger_v1"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _load_trajectory(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: {error}") from error
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            rows.append(row)
    return rows


def _png_header(path: Path) -> tuple[int, int, int, int]:
    with path.open("rb") as stream:
        header = stream.read(26)
    if len(header) < 26 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    return (
        int.from_bytes(header[16:20], "big"),
        int.from_bytes(header[20:24], "big"),
        header[24],
        header[25],
    )


def _episode_files(episode: Path) -> list[Path]:
    return [path for path in episode.rglob("*") if path.is_file()]


def _safe_vector(row: dict[str, Any], field: str, size: int) -> list[float]:
    value = row.get(field)
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{field} must contain {size} numbers")
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(float(item))
        for item in value
    ):
        raise ValueError(f"{field} must contain {size} finite numbers")
    return [float(item) for item in value]


def _validate_combat_ledger(
    row: dict[str, Any],
    frame: int,
    expected_shot_id: int,
    errors: list[str],
) -> tuple[int, bool, bool]:
    fire_pressed = row.get("fire_pressed")
    if not isinstance(fire_pressed, bool):
        errors.append(f"frame {frame} fire_pressed must be boolean")
        fire_pressed = False

    events = row.get("combat_events")
    if not isinstance(events, list):
        errors.append(f"frame {frame} combat_events must be an array")
        return expected_shot_id, False, False

    if not fire_pressed:
        if events:
            errors.append(
                f"frame {frame} has combat events without fire_pressed=true"
            )
        return expected_shot_id, False, False

    if len(events) != 3 or any(not isinstance(event, dict) for event in events):
        errors.append(
            f"frame {frame} combat event order must be fire, shot, hit or miss"
        )
        return expected_shot_id, True, False

    event_names = [event.get("event") for event in events]
    outcome_name = event_names[2]
    if (
        event_names[:2] != ["fire", "shot"]
        or not isinstance(outcome_name, str)
        or outcome_name not in {"hit", "miss"}
        or [event.get("sequence") for event in events] != [0, 1, 2]
    ):
        errors.append(
            f"frame {frame} combat event order must be fire, shot, hit or miss"
        )

    shot_ids = [event.get("shot_id") for event in events]
    if any(
        not isinstance(shot_id, int) or isinstance(shot_id, bool)
        for shot_id in shot_ids
    ):
        errors.append(f"frame {frame} combat shot_id must be an integer")
    elif len(set(shot_ids)) != 1 or shot_ids[0] != expected_shot_id:
        errors.append(
            f"frame {frame} combat shot_id is not the next sequential id "
            f"{expected_shot_id}"
        )

    shot = events[1]
    outcome = events[2]
    try:
        _safe_vector(shot, "origin_cm", 3)
        direction = _safe_vector(shot, "direction", 3)
        _safe_vector(outcome, "impact_position_cm", 3)
        if not math.isclose(
            math.sqrt(sum(value * value for value in direction)),
            1.0,
            abs_tol=1e-4,
        ):
            errors.append(f"frame {frame} shot direction must be unit length")
    except (TypeError, ValueError) as error:
        errors.append(f"frame {frame} combat event: {error}")

    distance = outcome.get("distance_cm")
    if (
        isinstance(distance, bool)
        or not isinstance(distance, (int, float))
        or not math.isfinite(float(distance))
        or float(distance) < 0
    ):
        errors.append(
            f"frame {frame} combat distance_cm must be a non-negative "
            "finite number"
        )

    hit = outcome_name == "hit"
    target_id = outcome.get("target_id")
    if hit and (not isinstance(target_id, str) or not target_id):
        errors.append(f"frame {frame} hit event requires target_id")
    if not hit and target_id is not None:
        errors.append(f"frame {frame} miss event target_id must be null")

    return expected_shot_id + 1, True, hit


def validate_episode(episode: Path | str) -> dict[str, Any]:
    episode = Path(episode).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {
        "path": str(episode),
        "episode_id": episode.name,
        "mode": "unknown",
        "seed": None,
        "course_hash": "",
        "end_reason": "",
        "complete": False,
        "schema_version": None,
        "trajectory_frames": 0,
        "capture_frames": 0,
        "capture_dropped": 0,
        "missing_capture_frames": 0,
        "shots_fired": 0,
        "shots_hit": 0,
        "shot_hit_rate": 0.0,
        "file_count": 0,
        "total_bytes": 0,
        "errors": errors,
        "warnings": warnings,
        "_rows": [],
        "_frame_times_ms": [],
        "_move_magnitudes": [],
        "_look_magnitudes": [],
        "_jump_values": [],
        "_collision_values": [],
        "_fire_values": [],
    }

    partial_manifest = episode / "manifest.partial.json"
    if partial_manifest.exists():
        errors.append("manifest.partial.json remains in the episode")

    manifest_path = episode / "manifest.json"
    if not manifest_path.exists():
        errors.append("manifest.json is missing")
        return result

    try:
        manifest = _load_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"manifest.json is invalid: {error}")
        return result

    result.update(
        {
            "episode_id": str(manifest.get("episode_id", episode.name)),
            "mode": str(manifest.get("mode", "unknown")),
            "seed": manifest.get("seed"),
            "course_hash": str(manifest.get("course_hash", "")),
            "end_reason": str(manifest.get("end_reason", "")),
            "complete": bool(manifest.get("complete", False)),
            "schema_version": manifest.get("schema_version"),
        }
    )
    if manifest.get("complete") is not True:
        errors.append("manifest complete flag is false")
    schema_version = manifest.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in SUPPORTED_SCHEMA_VERSIONS
    ):
        errors.append("unsupported manifest schema_version")
        schema_version = 1
    required_manifest_fields = {
        "episode_id",
        "mode",
        "seed",
        "parent_episode_id",
        "course_hash",
        "start_position_cm",
        "start_rotation_deg",
        "goal_position_cm",
        "engine_version",
        "git_revision",
        "simulation_hz",
        "capture_hz",
        "capture_interval_sim_frames",
        "capture_queue_capacity",
        "image_width",
        "image_height",
        "sensor_type",
        "depth_encoding",
        "depth_max_cm",
        "depth_decode_cm",
        "trajectory_frames",
        "capture_frames",
        "capture_dropped",
        "file_count",
        "total_bytes",
        "started_utc",
        "duration_s",
        "end_reason",
        "replay_name",
        "replay_archive_path",
        "complete",
    }
    if schema_version >= 2:
        required_manifest_fields.update(
            {
                "target_position_cm",
                "combat_contract",
                "primary_target_id",
                "shots_fired",
                "shots_hit",
                "shot_hit_rate",
            }
        )
    for field in sorted(required_manifest_fields - manifest.keys()):
        errors.append(f"manifest field is missing: {field}")
    if manifest.get("episode_id") != episode.name:
        errors.append("manifest episode_id does not match directory name")
    mode = manifest.get("mode")
    if not isinstance(mode, str) or mode not in VALID_MODES:
        errors.append("manifest mode is invalid")
    if (
        not isinstance(manifest.get("seed"), int)
        or isinstance(manifest.get("seed"), bool)
    ):
        errors.append("manifest seed must be an integer")
    if not isinstance(manifest.get("parent_episode_id"), str):
        errors.append("manifest parent_episode_id must be a string")
    if not isinstance(manifest.get("course_hash"), str) or not manifest.get(
        "course_hash"
    ):
        errors.append("manifest course_hash must be a non-empty string")
    for field, size in (
        ("start_position_cm", 3),
        ("start_rotation_deg", 3),
        ("goal_position_cm", 3),
    ):
        try:
            _safe_vector(manifest, field, size)
        except (TypeError, ValueError) as error:
            errors.append(f"manifest: {error}")
    if schema_version >= 2:
        try:
            _safe_vector(manifest, "target_position_cm", 3)
        except (TypeError, ValueError) as error:
            errors.append(f"manifest: {error}")
        if manifest.get("combat_contract") != COMBAT_CONTRACT:
            errors.append("manifest combat_contract is invalid")
        if (
            not isinstance(manifest.get("primary_target_id"), str)
            or not manifest.get("primary_target_id")
        ):
            errors.append("manifest primary_target_id must be a non-empty string")
    git_revision = manifest.get("git_revision")
    if (
        not isinstance(git_revision, str)
        or not git_revision
        or git_revision == "unavailable"
    ):
        errors.append("manifest git_revision is unavailable")
    if manifest.get("simulation_hz") != 30:
        errors.append("manifest simulation_hz must be 30")
    if manifest.get("capture_hz") not in (0, 10):
        errors.append("manifest capture_hz must be 0 or 10")
    if manifest.get("capture_interval_sim_frames") != 3:
        errors.append("manifest capture_interval_sim_frames must be 3")
    if manifest.get("capture_queue_capacity") != 8:
        errors.append("manifest capture_queue_capacity must be 8")
    if (manifest.get("image_width"), manifest.get("image_height")) != (320, 180):
        errors.append("manifest image dimensions must be 320x180")
    if manifest.get("sensor_type") != "scene_depth":
        errors.append("manifest sensor_type must be scene_depth")
    if manifest.get("depth_encoding") != "uint16_linear_cm":
        errors.append("manifest depth_encoding must be uint16_linear_cm")
    if manifest.get("depth_max_cm") != 2000:
        errors.append("manifest depth_max_cm must be 2000")
    manifest_end_reason = manifest.get("end_reason")
    if (
        not isinstance(manifest_end_reason, str)
        or manifest_end_reason not in VALID_END_REASONS
    ):
        errors.append("manifest end_reason is invalid")

    trajectory_path = episode / "trajectory.jsonl"
    if not trajectory_path.exists():
        errors.append("trajectory.jsonl is missing")
        return result

    try:
        rows = _load_trajectory(trajectory_path)
    except (OSError, ValueError) as error:
        errors.append(f"trajectory.jsonl is invalid: {error}")
        return result

    result["_rows"] = rows
    result["trajectory_frames"] = len(rows)
    simulation_hz = 30.0
    capture_hz = 10.0 if manifest.get("capture_hz") == 10 else 0.0
    expected_rgb: set[str] = set()
    expected_depth: set[str] = set()
    captured_count = 0
    dropped_count = 0
    missing_capture_count = 0
    expected_shot_id = 0
    shots_fired = 0
    shots_hit = 0

    for expected_frame, row in enumerate(rows):
        frame = row.get("sim_frame")
        if (
            not isinstance(frame, int)
            or isinstance(frame, bool)
            or frame != expected_frame
        ):
            errors.append(
                f"trajectory frame {expected_frame} has sim_frame={frame}"
            )
        timestamp = row.get("timestamp_s")
        expected_timestamp = expected_frame / simulation_hz
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(float(timestamp))
            or not math.isclose(
                float(timestamp), expected_timestamp, abs_tol=1e-5
            )
        ):
            errors.append(
                f"frame {expected_frame} timestamp mismatch: "
                f"{timestamp} != {expected_timestamp}"
            )

        delta = row.get("delta_s")
        expected_delta = 1.0 / simulation_hz
        if (
            isinstance(delta, bool)
            or not isinstance(delta, (int, float))
            or not math.isfinite(float(delta))
            or not math.isclose(float(delta), expected_delta, abs_tol=1e-5)
        ):
            errors.append(
                f"frame {expected_frame} delta mismatch: "
                f"{delta} != {expected_delta}"
            )
        if row.get("schema_version") != schema_version:
            errors.append(
                f"frame {expected_frame} schema_version does not match manifest"
            )

        if schema_version >= 2:
            (
                expected_shot_id,
                shot_fired,
                shot_hit,
            ) = _validate_combat_ledger(
                row,
                expected_frame,
                expected_shot_id,
                errors,
            )
            shots_fired += int(shot_fired)
            shots_hit += int(shot_hit)
            result["_fire_values"].append(shot_fired)
        else:
            result["_fire_values"].append(False)

        captured_value = row.get("captured")
        dropped_value = row.get("capture_dropped")
        if not isinstance(captured_value, bool):
            errors.append(f"frame {expected_frame} captured must be boolean")
        if not isinstance(dropped_value, bool):
            errors.append(f"frame {expected_frame} capture_dropped must be boolean")
        captured = captured_value is True
        dropped = dropped_value is True
        if captured and dropped:
            errors.append(f"frame {expected_frame} is both captured and dropped")
        if captured:
            captured_count += 1
            rgb_relative = row.get("rgb_path")
            depth_relative = row.get("depth_path")
            canonical_rgb = f"rgb/{expected_frame:06d}.png"
            canonical_depth = f"depth/{expected_frame:06d}.png"
            expected_rgb.add(canonical_rgb)
            expected_depth.add(canonical_depth)
            if not isinstance(rgb_relative, str) or not isinstance(depth_relative, str):
                errors.append(
                    f"frame {expected_frame} captured without both image paths"
                )
            else:
                normalized_rgb = rgb_relative.replace("\\", "/")
                normalized_depth = depth_relative.replace("\\", "/")
                if normalized_rgb != canonical_rgb:
                    errors.append(
                        f"frame {expected_frame} rgb_path mismatch: "
                        f"{normalized_rgb} != {canonical_rgb}"
                    )
                if normalized_depth != canonical_depth:
                    errors.append(
                        f"frame {expected_frame} depth_path mismatch: "
                        f"{normalized_depth} != {canonical_depth}"
                    )
            for relative in (canonical_rgb, canonical_depth):
                if not (episode / relative).exists():
                    errors.append(f"missing image: {relative}")
        elif row.get("rgb_path") is not None or row.get("depth_path") is not None:
            errors.append(
                f"frame {expected_frame} has image paths without captured=true"
            )
        if dropped:
            dropped_count += 1

        if capture_hz > 0:
            stride = max(1, round(simulation_hz / capture_hz))
            if expected_frame % stride == 0 and not captured and not dropped:
                missing_capture_count += 1
                errors.append(
                    f"expected capture at frame {expected_frame} is absent"
                )

        try:
            move = _safe_vector(row, "move_input", 2)
            look = _safe_vector(row, "look_input", 2)
            _safe_vector(row, "position_cm", 3)
            _safe_vector(row, "rotation_deg", 3)
            _safe_vector(row, "velocity_cm_s", 3)
            _safe_vector(row, "goal_relative_cm", 3)
        except (TypeError, ValueError) as error:
            errors.append(f"frame {expected_frame}: {error}")
            continue

        for field in ("jump_pressed", "collision", "done"):
            if not isinstance(row.get(field), bool):
                errors.append(f"frame {expected_frame} {field} must be boolean")
        end_reason = row.get("end_reason")
        if not isinstance(end_reason, str):
            errors.append(f"frame {expected_frame} end_reason must be a string")
        elif expected_frame < len(rows) - 1:
            if row.get("done") is True:
                errors.append(f"frame {expected_frame} has done=true before the end")
            if end_reason:
                errors.append(
                    f"frame {expected_frame} has an end_reason before the end"
                )

        result["_move_magnitudes"].append(math.hypot(*move))
        result["_look_magnitudes"].append(math.hypot(*look))
        result["_jump_values"].append(row.get("jump_pressed") is True)
        result["_collision_values"].append(row.get("collision") is True)
        frame_time = row.get("frame_time_ms")
        if (
            not isinstance(frame_time, bool)
            and isinstance(frame_time, (int, float))
            and math.isfinite(float(frame_time))
            and float(frame_time) >= 0
        ):
            result["_frame_times_ms"].append(float(frame_time))
        else:
            errors.append(
                f"frame {expected_frame} frame_time_ms must be a non-negative "
                "finite number"
            )

    result["capture_frames"] = captured_count
    result["capture_dropped"] = dropped_count
    result["missing_capture_frames"] = missing_capture_count
    result["shots_fired"] = shots_fired
    result["shots_hit"] = shots_hit
    result["shot_hit_rate"] = shots_hit / shots_fired if shots_fired else 0.0

    if not rows:
        errors.append("trajectory is empty")
    else:
        if rows[-1].get("done") is not True:
            errors.append("last trajectory row must have done=true")
        final_end_reason = rows[-1].get("end_reason")
        if (
            not isinstance(final_end_reason, str)
            or final_end_reason not in VALID_END_REASONS
        ):
            errors.append("last trajectory row has an invalid end_reason")
        if rows[-1].get("end_reason") != manifest.get("end_reason"):
            errors.append("trajectory and manifest end_reason differ")

    if manifest.get("trajectory_frames") != len(rows):
        errors.append(
            "manifest trajectory_frames does not match trajectory.jsonl"
        )
    if manifest.get("capture_frames") != captured_count:
        errors.append("manifest capture_frames does not match trajectory.jsonl")
    if manifest.get("capture_dropped") != dropped_count:
        errors.append("manifest capture_dropped does not match trajectory.jsonl")
    if schema_version >= 2:
        if manifest.get("shots_fired") != shots_fired:
            errors.append("manifest shots_fired does not match combat ledger")
        if manifest.get("shots_hit") != shots_hit:
            errors.append("manifest shots_hit does not match combat ledger")
        manifest_hit_rate = manifest.get("shot_hit_rate")
        if (
            isinstance(manifest_hit_rate, bool)
            or not isinstance(manifest_hit_rate, (int, float))
            or not math.isfinite(float(manifest_hit_rate))
            or not math.isclose(
                float(manifest_hit_rate),
                result["shot_hit_rate"],
                abs_tol=1e-9,
            )
        ):
            errors.append("manifest shot_hit_rate does not match combat ledger")
    if dropped_count:
        errors.append(f"episode contains {dropped_count} dropped captures")
    if capture_hz == 0 and captured_count:
        errors.append("capture_hz is zero but captured frames are present")
    duration = manifest.get("duration_s")
    expected_duration = len(rows) / simulation_hz
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or not math.isclose(float(duration), expected_duration, abs_tol=1e-5)
    ):
        errors.append("manifest duration_s does not match trajectory length")

    actual_rgb = {
        path.relative_to(episode).as_posix()
        for path in (episode / "rgb").glob("*.png")
    }
    actual_depth = {
        path.relative_to(episode).as_posix()
        for path in (episode / "depth").glob("*.png")
    }
    for orphan in sorted(actual_rgb - expected_rgb):
        errors.append(f"orphan RGB image: {orphan}")
    for orphan in sorted(actual_depth - expected_depth):
        errors.append(f"orphan Depth image: {orphan}")

    width = 320
    height = 180
    for relative in sorted(expected_rgb & actual_rgb):
        try:
            image_width, image_height, bit_depth, color_type = _png_header(
                episode / relative
            )
            if (image_width, image_height) != (width, height):
                errors.append(f"{relative} has incorrect dimensions")
            if bit_depth != 8 or color_type not in {2, 6}:
                errors.append(f"{relative} is not an 8-bit RGB/RGBA PNG")
        except (OSError, ValueError) as error:
            errors.append(f"{relative}: {error}")

    for relative in sorted(expected_depth & actual_depth):
        try:
            image_width, image_height, bit_depth, color_type = _png_header(
                episode / relative
            )
            if (image_width, image_height) != (width, height):
                errors.append(f"{relative} has incorrect dimensions")
            if bit_depth != 16 or color_type != 0:
                errors.append(f"{relative} is not a 16-bit grayscale PNG")
        except (OSError, ValueError) as error:
            errors.append(f"{relative}: {error}")

    replay_relative = manifest.get("replay_archive_path")
    if not isinstance(replay_relative, str):
        errors.append("manifest replay_archive_path is missing")
    else:
        replay_path = episode / replay_relative
        if not replay_path.exists() or replay_path.stat().st_size == 0:
            errors.append(f"native replay is missing or empty: {replay_relative}")

    files = _episode_files(episode)
    result["file_count"] = len(files)
    result["total_bytes"] = sum(path.stat().st_size for path in files)
    manifest_file_count = manifest.get("file_count")
    if (
        not isinstance(manifest_file_count, int)
        or isinstance(manifest_file_count, bool)
        or manifest_file_count != len(files)
    ):
        errors.append("manifest file_count does not match files on disk")
    manifest_total_bytes = manifest.get("total_bytes")
    if (
        not isinstance(manifest_total_bytes, (int, float))
        or isinstance(manifest_total_bytes, bool)
        or not math.isfinite(float(manifest_total_bytes))
        or float(manifest_total_bytes) != result["total_bytes"]
    ):
        errors.append("manifest total_bytes does not match files on disk")
    return result


def compare_episodes(
    original_episode: Path | str, replayed_episode: Path | str
) -> dict[str, Any]:
    original_episode = Path(original_episode).resolve()
    replayed_episode = Path(replayed_episode).resolve()
    original_manifest = _load_json(original_episode / "manifest.json")
    replayed_manifest = _load_json(replayed_episode / "manifest.json")
    original_rows = _load_trajectory(original_episode / "trajectory.jsonl")
    replayed_rows = _load_trajectory(replayed_episode / "trajectory.jsonl")

    original_by_frame = {
        int(row["sim_frame"]): np.asarray(row["position_cm"], dtype=float)
        for row in original_rows
    }
    replayed_by_frame = {
        int(row["sim_frame"]): np.asarray(row["position_cm"], dtype=float)
        for row in replayed_rows
    }
    shared_frames = sorted(original_by_frame.keys() & replayed_by_frame.keys())
    if not shared_frames:
        raise ValueError("episodes have no shared sim_frame values")

    errors = np.asarray(
        [
            np.linalg.norm(original_by_frame[frame] - replayed_by_frame[frame])
            for frame in shared_frames
        ],
        dtype=float,
    )
    start_error = float(errors[0])
    mean_error = float(np.mean(errors))
    p95_error = float(np.percentile(errors, 95))
    maximum_error = float(np.max(errors))
    final_error = float(errors[-1])
    seed_match = original_manifest.get("seed") == replayed_manifest.get("seed")
    original_episode_id = original_manifest.get("episode_id", original_episode.name)
    replayed_episode_id = replayed_manifest.get("episode_id", replayed_episode.name)
    parent_episode_match = (
        replayed_manifest.get("parent_episode_id") == original_episode_id
    )
    frame_alignment_match = (
        len(original_by_frame) == len(original_rows)
        and len(replayed_by_frame) == len(replayed_rows)
        and set(original_by_frame) == set(range(len(original_rows)))
        and set(replayed_by_frame) == set(range(len(replayed_rows)))
        and original_by_frame.keys() == replayed_by_frame.keys()
    )
    course_hash_match = (
        original_manifest.get("course_hash")
        == replayed_manifest.get("course_hash")
    )
    original_rows_by_frame = {
        int(row["sim_frame"]): row for row in original_rows
    }
    replayed_rows_by_frame = {
        int(row["sim_frame"]): row for row in replayed_rows
    }
    combat_mismatch_frames = [
        frame
        for frame in shared_frames
        if {
            "fire_pressed": original_rows_by_frame[frame].get(
                "fire_pressed", False
            ),
            "combat_events": original_rows_by_frame[frame].get(
                "combat_events", []
            ),
        }
        != {
            "fire_pressed": replayed_rows_by_frame[frame].get(
                "fire_pressed", False
            ),
            "combat_events": replayed_rows_by_frame[frame].get(
                "combat_events", []
            ),
        }
    ]
    missing_combat_frames = sorted(
        original_by_frame.keys() ^ replayed_by_frame.keys()
    )
    combat_mismatch_frames = sorted(
        set(combat_mismatch_frames + missing_combat_frames)
    )
    original_schema = original_manifest.get("schema_version")
    replayed_schema = replayed_manifest.get("schema_version")
    combat_event_applicable = (
        isinstance(original_schema, int)
        and not isinstance(original_schema, bool)
        and original_schema >= 2
        and isinstance(replayed_schema, int)
        and not isinstance(replayed_schema, bool)
        and replayed_schema >= 2
    )
    combat_event_match = (
        frame_alignment_match and not combat_mismatch_frames
    )
    within_target = (
        seed_match
        and parent_episode_match
        and frame_alignment_match
        and course_hash_match
        and start_error <= POSITION_TARGETS_CM["start"]
        and mean_error <= POSITION_TARGETS_CM["mean"]
        and p95_error <= POSITION_TARGETS_CM["p95"]
        and final_error <= POSITION_TARGETS_CM["final"]
        and combat_event_match
    )
    return {
        "original_episode_id": original_episode_id,
        "replayed_episode_id": replayed_episode_id,
        "seed": original_manifest.get("seed"),
        "shared_frames": len(shared_frames),
        "seed_match": seed_match,
        "parent_episode_match": parent_episode_match,
        "frame_alignment_match": frame_alignment_match,
        "course_hash_match": course_hash_match,
        "combat_event_applicable": combat_event_applicable,
        "combat_event_match": combat_event_match,
        "combat_mismatch_frames": combat_mismatch_frames,
        "original_shots_fired": sum(
            row.get("fire_pressed") is True for row in original_rows
        ),
        "replayed_shots_fired": sum(
            row.get("fire_pressed") is True for row in replayed_rows
        ),
        "start_position_error_cm": start_error,
        "mean_position_error_cm": mean_error,
        "p95_position_error_cm": p95_error,
        "max_position_error_cm": maximum_error,
        "final_position_error_cm": final_error,
        "within_target": bool(within_target),
        "targets_cm": POSITION_TARGETS_CM,
        "frame_errors_cm": errors.tolist(),
    }


def _distribution(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0}
    array = np.asarray(values, dtype=float)
    return {
        "count": int(array.size),
        "mean": float(np.mean(array)),
        "p50": float(np.percentile(array, 50)),
        "p95": float(np.percentile(array, 95)),
    }


def build_report(
    episodes_root: Path | str, output_directory: Path | str | None = None
) -> dict[str, Any]:
    episodes_root = Path(episodes_root).resolve()
    output = (
        Path(output_directory).resolve()
        if output_directory
        else episodes_root.parent / "reports"
    )
    output.mkdir(parents=True, exist_ok=True)

    episode_paths = sorted(
        path
        for path in episodes_root.iterdir()
        if path.is_dir()
        and (
            (path / "manifest.json").exists()
            or (path / "manifest.partial.json").exists()
        )
    )
    results = [validate_episode(path) for path in episode_paths]
    manifests: dict[str, dict[str, Any]] = {}
    paths_by_id: dict[str, Path] = {}
    for path, result in zip(episode_paths, results, strict=True):
        manifest_path = path / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = _load_json(manifest_path)
            except (OSError, ValueError, json.JSONDecodeError):
                result["_capture_hz"] = 0.0
                continue
            episode_id = str(manifest.get("episode_id", result["episode_id"]))
            manifests[episode_id] = manifest
            paths_by_id[episode_id] = path
            capture_hz = manifest.get("capture_hz", 0)
            result["_capture_hz"] = (
                float(capture_hz)
                if not isinstance(capture_hz, bool)
                and isinstance(capture_hz, (int, float))
                else 0.0
            )
        else:
            result["_capture_hz"] = 0.0

    comparisons: list[dict[str, Any]] = []
    for result in results:
        if result["mode"] != "input-replay":
            continue
        manifest = manifests.get(result["episode_id"], {})
        parent_id = str(manifest.get("parent_episode_id", ""))
        parent_path = paths_by_id.get(parent_id)
        replayed_path = paths_by_id.get(result["episode_id"])
        if parent_path and replayed_path:
            try:
                comparisons.append(compare_episodes(parent_path, replayed_path))
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                json.JSONDecodeError,
            ) as error:
                result["errors"].append(f"replay comparison failed: {error}")
        else:
            result["warnings"].append(
                f"parent episode is unavailable for comparison: {parent_id}"
            )

    modes = Counter(result["mode"] for result in results)
    outcomes = Counter(result["end_reason"] for result in results)
    total_errors = sum(len(result["errors"]) for result in results)
    total_warnings = sum(len(result["warnings"]) for result in results)

    action_metrics: dict[str, Any] = {}
    for mode in sorted(modes):
        matching = [result for result in results if result["mode"] == mode]
        moves = [
            value for result in matching for value in result["_move_magnitudes"]
        ]
        looks = [
            value for result in matching for value in result["_look_magnitudes"]
        ]
        jumps = [
            value for result in matching for value in result["_jump_values"]
        ]
        collisions = [
            value for result in matching for value in result["_collision_values"]
        ]
        fires = [
            value for result in matching for value in result["_fire_values"]
        ]
        action_metrics[mode] = {
            "move_magnitude": _distribution(moves),
            "look_magnitude": _distribution(looks),
            "jump_rate": statistics.fmean(jumps) if jumps else 0.0,
            "collision_rate": statistics.fmean(collisions)
            if collisions
            else 0.0,
            "fire_rate": statistics.fmean(fires) if fires else 0.0,
        }

    combat_by_mode: dict[str, dict[str, int | float]] = {}
    for mode in sorted(modes):
        matching = [result for result in results if result["mode"] == mode]
        mode_shots = sum(result["shots_fired"] for result in matching)
        mode_hits = sum(result["shots_hit"] for result in matching)
        combat_by_mode[mode] = {
            "shots_fired": mode_shots,
            "shots_hit": mode_hits,
            "shot_hit_rate": mode_hits / mode_shots if mode_shots else 0.0,
        }
    total_shots = sum(result["shots_fired"] for result in results)
    total_hits = sum(result["shots_hit"] for result in results)
    combat_comparisons = [
        comparison
        for comparison in comparisons
        if comparison["combat_event_applicable"]
    ]
    combat = {
        "contract": COMBAT_CONTRACT,
        "shots_fired": total_shots,
        "shots_hit": total_hits,
        "shot_hit_rate": total_hits / total_shots if total_shots else 0.0,
        "by_mode": combat_by_mode,
        "replay_event_matches": sum(
            comparison["combat_event_match"]
            for comparison in combat_comparisons
        ),
        "replay_event_comparisons": len(combat_comparisons),
    }

    capture_on, capture_off = bot_frame_times_by_capture(results)
    performance = {
        "capture_on_ms": _distribution(capture_on),
        "capture_off_ms": _distribution(capture_off),
        "median_fps_drop_percent": None,
    }
    if capture_on and capture_off:
        on_median = float(np.median(capture_on))
        off_median = float(np.median(capture_off))
        if on_median > 0 and off_median > 0:
            performance["median_fps_drop_percent"] = (
                1.0 - (1000.0 / on_median) / (1000.0 / off_median)
            ) * 100.0

    seed_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        if isinstance(result["seed"], int):
            seed_groups[result["seed"]].append(result)
    seed_reproducibility = {
        str(seed): {
            "episodes": len(group),
            "course_hash_match": len(
                {result["course_hash"] for result in group}
            )
            <= 1,
        }
        for seed, group in seed_groups.items()
    }

    plot_files = write_plots(output, results, comparisons)
    public_results = []
    for result in results:
        public_results.append(
            {key: value for key, value in result.items() if not key.startswith("_")}
        )

    public_comparisons = [
        {
            key: value
            for key, value in comparison.items()
            if key != "frame_errors_cm"
        }
        for comparison in comparisons
    ]
    summary = {
        "schema_version": 2,
        "episodes_root": str(episodes_root),
        "episode_count": len(results),
        "valid_episode_count": sum(not result["errors"] for result in results),
        "error_count": total_errors,
        "warning_count": total_warnings,
        "modes": dict(modes),
        "outcomes": dict(outcomes),
        "total_bytes": sum(result["total_bytes"] for result in results),
        "missing_capture_frames": sum(
            result["missing_capture_frames"] for result in results
        ),
        "capture_dropped": sum(result["capture_dropped"] for result in results),
        "seed_reproducibility": seed_reproducibility,
        "replay_comparisons": public_comparisons,
        "action_metrics": action_metrics,
        "combat": combat,
        "performance": performance,
        "plots": plot_files,
        "episodes": public_results,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _write_markdown_summary(output / "summary.md", summary)
    return summary


def _write_markdown_summary(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Unreal SimTrace dataset report",
        "",
        f"- Episodes: {summary['episode_count']}",
        f"- Valid episodes: {summary['valid_episode_count']}",
        f"- Validation errors: {summary['error_count']}",
        f"- Missing capture frames: {summary['missing_capture_frames']}",
        f"- Dropped captures: {summary['capture_dropped']}",
        f"- Total size: {summary['total_bytes'] / (1024 * 1024):.2f} MiB",
        "",
        "## Episodes",
        "",
        "| Episode | Mode | Seed | Frames | Captures | End | Errors |",
        "|---|---:|---:|---:|---:|---|---:|",
    ]
    for episode in summary["episodes"]:
        lines.append(
            f"| {episode['episode_id']} | {episode['mode']} | "
            f"{episode['seed']} | {episode['trajectory_frames']} | "
            f"{episode['capture_frames']} | {episode['end_reason']} | "
            f"{len(episode['errors'])} |"
        )

    lines.extend(
        [
            "",
            "## Seed reproducibility",
            "",
            "| Seed | Episodes | Course hash match |",
            "|---:|---:|---|",
        ]
    )
    for seed, result in summary["seed_reproducibility"].items():
        lines.append(
            f"| {seed} | {result['episodes']} | "
            f"{'pass' if result['course_hash_match'] else 'fail'} |"
        )

    lines.extend(
        [
            "",
            "## Action distributions",
            "",
            "| Mode | Samples | Move mean | Move p95 | Look mean | Look p95 | "
            "Jump rate | Fire rate | Collision rate |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for mode, metrics in summary["action_metrics"].items():
        move = metrics["move_magnitude"]
        look = metrics["look_magnitude"]
        lines.append(
            f"| {mode} | {move['count']} | {move['mean']:.3f} | "
            f"{move['p95']:.3f} | {look['mean']:.3f} | {look['p95']:.3f} | "
            f"{metrics['jump_rate'] * 100.0:.2f}% | "
            f"{metrics['fire_rate'] * 100.0:.2f}% | "
            f"{metrics['collision_rate'] * 100.0:.2f}% |"
        )

    combat = summary["combat"]
    lines.extend(
        [
            "",
            "## One Bullet Outcome Ledger",
            "",
            f"- Contract: `{combat['contract']}`",
            f"- Shots: {combat['shots_fired']}",
            f"- Hits: {combat['shots_hit']}",
            f"- Hit rate: {combat['shot_hit_rate'] * 100.0:.2f}%",
            f"- Exact replay event matches: "
            f"{combat['replay_event_matches']}/"
            f"{combat['replay_event_comparisons']}",
            "",
            "| Mode | Shots | Hits | Hit rate |",
            "|---|---:|---:|---:|",
        ]
    )
    for mode, metrics in combat["by_mode"].items():
        lines.append(
            f"| {mode} | {metrics['shots_fired']} | "
            f"{metrics['shots_hit']} | "
            f"{metrics['shot_hit_rate'] * 100.0:.2f}% |"
        )

    lines.extend(
        [
            "",
            "## JSON replay comparisons",
            "",
            "| Replay | Frames | Mean cm | P95 cm | Final cm | Combat | Target |",
            "|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for comparison in summary["replay_comparisons"]:
        combat_result = (
            "pass"
            if comparison["combat_event_match"]
            else "fail"
        )
        if not comparison["combat_event_applicable"]:
            combat_result = "n/a"
        lines.append(
            f"| {comparison['replayed_episode_id']} | "
            f"{comparison['shared_frames']} | "
            f"{comparison['mean_position_error_cm']:.3f} | "
            f"{comparison['p95_position_error_cm']:.3f} | "
            f"{comparison['final_position_error_cm']:.3f} | "
            f"{combat_result} | "
            f"{'pass' if comparison['within_target'] else 'fail'} |"
        )

    performance = summary["performance"]
    capture_off = performance["capture_off_ms"]
    capture_on = performance["capture_on_ms"]
    lines.extend(
        [
            "",
            "## Capture performance",
            "",
            "| Metric | Capture off | Capture on |",
            "|---|---:|---:|",
            f"| Samples | {capture_off['count']} | {capture_on['count']} |",
            f"| Mean frame time ms | {capture_off['mean']:.3f} | "
            f"{capture_on['mean']:.3f} |",
            f"| Median frame time ms | {capture_off['p50']:.3f} | "
            f"{capture_on['p50']:.3f} |",
            f"| P95 frame time ms | {capture_off['p95']:.3f} | "
            f"{capture_on['p95']:.3f} |",
            "",
            "Median FPS drop: "
            + (
                f"{performance['median_fps_drop_percent']:.3f}%"
                if performance["median_fps_drop_percent"] is not None
                else "not available"
            ),
            "",
            "## Plots",
            "",
        ]
    )
    for plot in summary["plots"]:
        label = Path(plot).stem.replace("_", " ").title()
        lines.append(f"![{label}]({plot})")
        lines.append("")

    lines.extend(["", "## Validation issues", ""])
    issue_count = 0
    for episode in summary["episodes"]:
        for error in episode["errors"]:
            issue_count += 1
            lines.append(f"- {episode['episode_id']}: {error}")
    if issue_count == 0:
        lines.append("- None")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _validate_command(path: Path) -> int:
    if (path / "manifest.json").exists() or (
        path / "manifest.partial.json"
    ).exists():
        results = [validate_episode(path)]
    else:
        results = [
            validate_episode(episode)
            for episode in sorted(path.iterdir())
            if episode.is_dir()
        ]
    public = [
        {key: value for key, value in result.items() if not key.startswith("_")}
        for result in results
    ]
    print(json.dumps(public, indent=2, ensure_ascii=False))
    return 1 if any(result["errors"] for result in results) else 0


def _compare_command(
    original: Path, replayed: Path, output: Path | None
) -> int:
    metrics = compare_episodes(original, replayed)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0 if metrics["within_target"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and report Unreal SimTrace datasets"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("path", type=Path)

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("original", type=Path)
    compare_parser.add_argument("replayed", type=Path)
    compare_parser.add_argument("--output", type=Path)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("episodes_root", type=Path)
    report_parser.add_argument("--output-dir", type=Path)

    arguments = parser.parse_args(argv)
    if arguments.command == "validate":
        return _validate_command(arguments.path)
    if arguments.command == "compare":
        return _compare_command(
            arguments.original, arguments.replayed, arguments.output
        )
    if arguments.command == "report":
        summary = build_report(arguments.episodes_root, arguments.output_dir)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 1 if summary["error_count"] else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
