from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

if __package__:
    from .validate_dataset import _load_json as _load_validated_json
    from .validate_dataset import _load_trajectory, validate_episode
else:
    from validate_dataset import _load_json as _load_validated_json
    from validate_dataset import _load_trajectory, validate_episode


DATASET_SCHEMA_VERSION = 1
DEFAULT_SPLIT_SEED = 20260731
STATE_FEATURES = (
    "position_x_cm",
    "position_y_cm",
    "position_z_cm",
    "rotation_pitch_deg",
    "rotation_yaw_deg",
    "rotation_roll_deg",
    "velocity_x_cm_s",
    "velocity_y_cm_s",
    "velocity_z_cm_s",
    "goal_relative_x_cm",
    "goal_relative_y_cm",
    "goal_relative_z_cm",
)
ACTION_FEATURES = (
    "move_right",
    "move_forward",
    "look_yaw",
    "look_pitch",
    "jump_pressed",
    "fire_pressed",
)


class DatasetExportError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return _load_validated_json(path)
    except ValueError as error:
        raise DatasetExportError(str(error)) from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _source_episode_paths(episodes_root: Path) -> list[Path]:
    episode_paths = sorted(
        path
        for path in episodes_root.iterdir()
        if path.is_dir()
        and (
            (path / "manifest.json").exists()
            or (path / "manifest.partial.json").exists()
        )
    )
    if not episode_paths:
        raise DatasetExportError(f"no episodes found in {episodes_root}")

    interrupted: list[str] = []
    for episode_path in episode_paths:
        if _is_link_or_reparse(episode_path):
            interrupted.append(f"{episode_path.name}: episode directory is a link")
            continue
        partial = episode_path / "manifest.partial.json"
        final = episode_path / "manifest.json"
        if partial.exists():
            interrupted.append(f"{episode_path.name}: manifest.partial.json remains")
        if not final.is_file():
            interrupted.append(f"{episode_path.name}: manifest.json is missing")
        elif _is_link_or_reparse(final):
            interrupted.append(f"{episode_path.name}: manifest.json is a link")
    if interrupted:
        raise DatasetExportError(
            "dataset export requires complete episodes: " + " | ".join(interrupted)
        )
    return episode_paths


def source_manifest_set_sha256(episodes_root: Path | str) -> str:
    episodes_root = Path(episodes_root).resolve()
    episode_paths = _source_episode_paths(episodes_root)
    digest = hashlib.sha256()
    for episode_path in episode_paths:
        digest.update(episode_path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(episode_path / "manifest.json").encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _stable_seed_rank(seed: int, split_seed: int) -> bytes:
    return hashlib.sha256(f"{split_seed}:{seed}".encode()).digest()


def assign_seed_splits(
    seeds: list[int] | tuple[int, ...] | set[int],
    split_seed: int = DEFAULT_SPLIT_SEED,
) -> dict[int, str]:
    if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds):
        raise DatasetExportError("every split seed group must be an integer")
    unique_seeds = sorted(set(seeds))
    if not unique_seeds:
        return {}

    ranked = sorted(
        unique_seeds,
        key=lambda seed: (_stable_seed_rank(seed, split_seed), seed),
    )
    if len(ranked) == 1:
        return {ranked[0]: "train"}
    if len(ranked) == 2:
        return {ranked[0]: "train", ranked[1]: "test"}

    validation_count = max(1, round(len(ranked) * 0.1))
    test_count = max(1, round(len(ranked) * 0.1))
    train_count = len(ranked) - validation_count - test_count
    if train_count < 1:
        train_count = 1
        validation_count = 1
        test_count = len(ranked) - 2

    assignments: dict[int, str] = {}
    for index, seed in enumerate(ranked):
        if index < train_count:
            split = "train"
        elif index < train_count + validation_count:
            split = "validation"
        else:
            split = "test"
        assignments[seed] = split
    return assignments


def _state_vector(row: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for field in (
        "position_cm",
        "rotation_deg",
        "velocity_cm_s",
        "goal_relative_cm",
    ):
        field_values = row[field]
        values.extend(float(value) for value in field_values)
    return values


def _action_vector(row: dict[str, Any]) -> list[float]:
    return [
        *(float(value) for value in row["move_input"]),
        *(float(value) for value in row["look_input"]),
        1.0 if row["jump_pressed"] else 0.0,
        1.0 if row.get("fire_pressed", False) else 0.0,
    ]


def _sensor_path(episode_id: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return (Path(episode_id) / Path(value)).as_posix()


def _source_root_reference(episodes_root: Path, output: Path) -> str:
    try:
        relative = os.path.relpath(episodes_root, output)
    except ValueError:
        return str(episodes_root)
    return Path(relative).as_posix()


def _generation_sha256(manifest: dict[str, Any]) -> str:
    generation = {
        "source_manifest_set_sha256": manifest["source_manifest_set_sha256"],
        "included_modes": manifest["included_modes"],
        "split_seed": manifest["split"]["split_seed"],
        "file_integrity": manifest["file_integrity"],
    }
    encoded = json.dumps(
        generation,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _replace_directory(candidate: Path, output: Path, transaction_root: Path) -> None:
    previous = transaction_root / "previous"
    had_previous = output.exists()
    if had_previous:
        output.replace(previous)
    try:
        candidate.replace(output)
    except BaseException:
        if had_previous and previous.exists():
            previous.replace(output)
        raise


def export_dataset(
    episodes_root: Path | str,
    output_directory: Path | str,
    split_seed: int = DEFAULT_SPLIT_SEED,
    include_input_replay: bool = False,
) -> dict[str, Any]:
    episodes_root = Path(episodes_root).resolve()
    output_path = Path(output_directory).absolute()
    if _is_link_or_reparse(output_path):
        raise DatasetExportError(f"dataset output must not be a link: {output_path}")
    output = output_path.resolve()
    if not episodes_root.is_dir():
        raise DatasetExportError(f"episodes root does not exist: {episodes_root}")
    if (
        output == episodes_root
        or output in episodes_root.parents
        or episodes_root in output.parents
    ):
        raise DatasetExportError(
            "dataset output must not contain or be contained by the episodes root"
        )
    if output.exists() and not output.is_dir():
        raise DatasetExportError(f"dataset output is not a directory: {output}")

    episode_paths = _source_episode_paths(episodes_root)
    source_manifest_fingerprint = source_manifest_set_sha256(episodes_root)

    episodes: list[tuple[Path, dict[str, Any]]] = []
    invalid: list[str] = []
    for episode_path in episode_paths:
        result = validate_episode(episode_path)
        if result["errors"]:
            invalid.append(f"{episode_path.name}: " + "; ".join(result["errors"][:3]))
            continue
        manifest = result.get("_manifest")
        if not isinstance(manifest, dict):
            invalid.append(f"{episode_path.name}: validated manifest is unavailable")
            continue
        episodes.append((episode_path, manifest))
    if invalid:
        raise DatasetExportError(
            "dataset export requires valid episodes: " + " | ".join(invalid)
        )

    source_episode_count = len(episodes)
    included_modes = {"human", "bot"}
    if include_input_replay:
        included_modes.add("input-replay")
    included_episodes = [
        episode for episode in episodes if episode[1].get("mode") in included_modes
    ]
    if not included_episodes:
        raise DatasetExportError("no episodes match the requested training modes")
    excluded_episode_count = source_episode_count - len(included_episodes)

    seed_splits = assign_seed_splits(
        [int(manifest["seed"]) for _, manifest in included_episodes],
        split_seed,
    )
    episode_split_counts: Counter[str] = Counter()
    transition_split_counts: Counter[str] = Counter()
    sensor_split_counts: Counter[str] = Counter()
    engine_versions = {str(manifest["engine_version"]) for _, manifest in episodes}
    git_revisions = {str(manifest["git_revision"]) for _, manifest in episodes}
    transition_count = 0
    sensor_policy_sample_count = 0

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}-", dir=output.parent
    ) as temporary:
        transaction_root = Path(temporary)
        candidate = transaction_root / "dataset"
        candidate.mkdir()
        transitions_path = candidate / "transitions.jsonl"
        sensor_path = candidate / "sensor_policy.jsonl"
        with (
            transitions_path.open(
                "w", encoding="utf-8", newline="\n"
            ) as transitions_stream,
            sensor_path.open("w", encoding="utf-8", newline="\n") as sensor_stream,
        ):
            for episode_path, manifest in included_episodes:
                episode_id = str(manifest["episode_id"])
                seed = int(manifest["seed"])
                split = seed_splits[seed]
                episode_split_counts[split] += 1
                rows = _load_trajectory(episode_path / "trajectory.jsonl")

                for observation, outcome in pairwise(rows):
                    transition = {
                        "schema_version": DATASET_SCHEMA_VERSION,
                        "transition_id": (
                            f"{episode_id}:{int(outcome['sim_frame']):06d}"
                        ),
                        "split": split,
                        "episode_id": episode_id,
                        "mode": manifest["mode"],
                        "seed": seed,
                        "course_hash": manifest["course_hash"],
                        "observation_sim_frame": int(observation["sim_frame"]),
                        "action_sim_frame": int(outcome["sim_frame"]),
                        "outcome_sim_frame": int(outcome["sim_frame"]),
                        "observation_state": _state_vector(observation),
                        "action": _action_vector(outcome),
                        "outcome_state": _state_vector(outcome),
                        "rgb_path": _sensor_path(
                            episode_id, observation.get("rgb_path")
                        ),
                        "depth_path": _sensor_path(
                            episode_id, observation.get("depth_path")
                        ),
                        "collision": bool(outcome["collision"]),
                        "combat_events": outcome.get("combat_events", []),
                        "done": bool(outcome["done"]),
                        "end_reason": str(outcome["end_reason"]),
                    }
                    serialized = (
                        json.dumps(
                            transition,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    transitions_stream.write(serialized)
                    transition_count += 1
                    transition_split_counts[split] += 1
                    if transition["rgb_path"] and transition["depth_path"]:
                        sensor_stream.write(serialized)
                        sensor_policy_sample_count += 1
                        sensor_split_counts[split] += 1

        manifest = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "dataset_type": "simtrace_causal_transition_index",
            "source_episodes_root": _source_root_reference(episodes_root, output),
            "source_episode_count": source_episode_count,
            "source_manifest_set_sha256": source_manifest_fingerprint,
            "episode_count": len(included_episodes),
            "excluded_episode_count": excluded_episode_count,
            "included_modes": sorted(included_modes),
            "transition_count": transition_count,
            "sensor_policy_sample_count": sensor_policy_sample_count,
            "state_features": list(STATE_FEATURES),
            "action_features": list(ACTION_FEATURES),
            "split": {
                "unit": "seed",
                "algorithm": (
                    "sha256(split_seed:course_seed) rank with 80/10/10 allocation"
                ),
                "split_seed": split_seed,
                "seed_assignments": {
                    str(seed): split for seed, split in sorted(seed_splits.items())
                },
                "episode_counts": dict(sorted(episode_split_counts.items())),
                "transition_counts": dict(sorted(transition_split_counts.items())),
                "sensor_policy_sample_counts": dict(
                    sorted(sensor_split_counts.items())
                ),
            },
            "causal_alignment": {
                "observation": "PostPhysics state and sensor from frame t",
                "action": "PrePhysics input applied at frame t+1",
                "outcome": "PostPhysics state produced at frame t+1",
                "same_frame_sensor_action_pairing": False,
            },
            "sensor": {
                "rgb": "320x180 RGB uint8",
                "depth": "320x180 uint16 linear centimeters",
                "depth_max_cm": 2000,
                "invalid_depth_value": 0,
            },
            "source_engine_versions": sorted(engine_versions),
            "source_git_revisions": sorted(git_revisions),
            "files": {
                "transitions": "transitions.jsonl",
                "sensor_policy": "sensor_policy.jsonl",
            },
            "file_integrity": {
                "transitions": {
                    "sha256": _sha256(transitions_path),
                    "record_count": transition_count,
                },
                "sensor_policy": {
                    "sha256": _sha256(sensor_path),
                    "record_count": sensor_policy_sample_count,
                },
            },
            "complete": True,
        }
        manifest["generation_sha256"] = _generation_sha256(manifest)
        (candidate / "dataset.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        validate_dataset_index(
            candidate,
            expected_episodes_root=episodes_root,
            source_reference_base=output,
        )
        _replace_directory(candidate, output, transaction_root)
        return manifest


def _resolve_source_path(source_root: Path, relative_path: str) -> Path:
    resolved = (source_root / Path(relative_path)).resolve()
    try:
        resolved.relative_to(source_root)
    except ValueError as error:
        raise DatasetExportError(
            f"sample path escapes the episodes root: {relative_path}"
        ) from error
    return resolved


def _resolve_dataset_member(
    dataset_directory: Path, value: object, expected_name: str
) -> Path:
    if not isinstance(value, str) or value != expected_name:
        raise DatasetExportError(f"dataset member must be {expected_name}: {value!r}")
    relative = Path(value)
    if relative.is_absolute():
        raise DatasetExportError(f"dataset member must be relative: {value}")
    member = dataset_directory / relative
    if _is_link_or_reparse(member):
        raise DatasetExportError(f"dataset member must not be a link: {value}")
    resolved = member.resolve()
    try:
        resolved.relative_to(dataset_directory)
    except ValueError as error:
        raise DatasetExportError(
            f"dataset member escapes the dataset directory: {value}"
        ) from error
    if not resolved.is_file():
        raise DatasetExportError(f"dataset member is missing: {value}")
    return resolved


def _require_exact_keys(
    value: object, expected: set[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatasetExportError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise DatasetExportError(
            f"{label} fields do not match schema "
            f"(missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)})"
        )
    return value


def _load_index(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise DatasetExportError(
                    f"{path}:{line_number} contains invalid JSON"
                ) from error
            if not isinstance(value, dict):
                raise DatasetExportError(f"{path}:{line_number} must contain an object")
            records.append(value)
    return records


def _require_non_negative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DatasetExportError(f"{label} must be a non-negative integer")
    return value


def validate_dataset_index(
    dataset_directory: Path | str,
    *,
    expected_episodes_root: Path | str | None = None,
    source_reference_base: Path | str | None = None,
) -> dict[str, Any]:
    unresolved_directory = Path(dataset_directory).absolute()
    if _is_link_or_reparse(unresolved_directory):
        raise DatasetExportError("dataset directory must not be a link")
    dataset_directory = unresolved_directory.resolve()
    if not dataset_directory.is_dir():
        raise DatasetExportError(
            f"dataset directory does not exist: {dataset_directory}"
        )
    manifest_path = dataset_directory / "dataset.json"
    if _is_link_or_reparse(manifest_path):
        raise DatasetExportError("dataset.json must not be a link")
    manifest = _load_json(manifest_path)
    _require_exact_keys(
        manifest,
        {
            "schema_version",
            "dataset_type",
            "source_episodes_root",
            "source_episode_count",
            "source_manifest_set_sha256",
            "episode_count",
            "excluded_episode_count",
            "included_modes",
            "transition_count",
            "sensor_policy_sample_count",
            "state_features",
            "action_features",
            "split",
            "causal_alignment",
            "sensor",
            "source_engine_versions",
            "source_git_revisions",
            "files",
            "file_integrity",
            "generation_sha256",
            "complete",
        },
        "dataset manifest",
    )
    if manifest["schema_version"] != DATASET_SCHEMA_VERSION:
        raise DatasetExportError("dataset schema_version is unsupported")
    if manifest["dataset_type"] != "simtrace_causal_transition_index":
        raise DatasetExportError("dataset_type is invalid")
    if manifest["complete"] is not True:
        raise DatasetExportError("dataset manifest is incomplete")

    source_reference = manifest["source_episodes_root"]
    if not isinstance(source_reference, str) or not source_reference:
        raise DatasetExportError("dataset source_episodes_root is missing")
    reference_base = (
        Path(source_reference_base).resolve()
        if source_reference_base is not None
        else dataset_directory
    )
    source_root = (reference_base / Path(source_reference)).resolve()
    if expected_episodes_root is not None:
        expected_root = Path(expected_episodes_root).resolve()
        if source_root != expected_root:
            raise DatasetExportError(
                "dataset source_episodes_root does not match the evidence tree"
            )
    if not source_root.is_dir():
        raise DatasetExportError(f"dataset source root is missing: {source_root}")

    source_paths = _source_episode_paths(source_root)
    source_count = _require_non_negative_integer(
        manifest["source_episode_count"], "source_episode_count"
    )
    if source_count != len(source_paths):
        raise DatasetExportError("source_episode_count does not match source tree")
    source_fingerprint = source_manifest_set_sha256(source_root)
    if manifest["source_manifest_set_sha256"] != source_fingerprint:
        raise DatasetExportError(
            "source_manifest_set_sha256 does not match source tree"
        )

    source_manifests = [_load_json(path / "manifest.json") for path in source_paths]
    source_ids = {str(value.get("episode_id")) for value in source_manifests}
    if len(source_ids) != len(source_paths) or any(
        manifest.get("episode_id") != path.name
        for path, manifest in zip(source_paths, source_manifests, strict=True)
    ):
        raise DatasetExportError("source episode identifiers are invalid")
    engine_versions = sorted(
        {str(value.get("engine_version")) for value in source_manifests}
    )
    git_revisions = sorted(
        {str(value.get("git_revision")) for value in source_manifests}
    )
    if manifest["source_engine_versions"] != engine_versions:
        raise DatasetExportError("source_engine_versions do not match source tree")
    if manifest["source_git_revisions"] != git_revisions:
        raise DatasetExportError("source_git_revisions do not match source tree")

    files = _require_exact_keys(
        manifest["files"], {"transitions", "sensor_policy"}, "files"
    )
    integrity = _require_exact_keys(
        manifest["file_integrity"],
        {"transitions", "sensor_policy"},
        "file_integrity",
    )
    transitions_path = _resolve_dataset_member(
        dataset_directory, files["transitions"], "transitions.jsonl"
    )
    sensor_path = _resolve_dataset_member(
        dataset_directory, files["sensor_policy"], "sensor_policy.jsonl"
    )
    transitions = _load_index(transitions_path)
    sensor_records = _load_index(sensor_path)

    for key, path, records in (
        ("transitions", transitions_path, transitions),
        ("sensor_policy", sensor_path, sensor_records),
    ):
        item = _require_exact_keys(
            integrity[key], {"sha256", "record_count"}, f"file_integrity.{key}"
        )
        if item["sha256"] != _sha256(path):
            raise DatasetExportError(f"{key} sha256 does not match file")
        if _require_non_negative_integer(
            item["record_count"], f"file_integrity.{key}.record_count"
        ) != len(records):
            raise DatasetExportError(f"{key} record_count does not match file")

    transition_count = _require_non_negative_integer(
        manifest["transition_count"], "transition_count"
    )
    sensor_count = _require_non_negative_integer(
        manifest["sensor_policy_sample_count"],
        "sensor_policy_sample_count",
    )
    if transition_count != len(transitions):
        raise DatasetExportError("transition_count does not match index")
    if sensor_count != len(sensor_records):
        raise DatasetExportError("sensor_policy_sample_count does not match index")

    state_features = manifest["state_features"]
    action_features = manifest["action_features"]
    if state_features != list(STATE_FEATURES):
        raise DatasetExportError("state_features do not match exporter schema")
    if action_features != list(ACTION_FEATURES):
        raise DatasetExportError("action_features do not match exporter schema")
    included_modes = manifest["included_modes"]
    if (
        not isinstance(included_modes, list)
        or not included_modes
        or any(mode not in {"human", "bot", "input-replay"} for mode in included_modes)
        or len(set(included_modes)) != len(included_modes)
        or included_modes != sorted(included_modes)
    ):
        raise DatasetExportError("included_modes are invalid")

    split = _require_exact_keys(
        manifest["split"],
        {
            "unit",
            "algorithm",
            "split_seed",
            "seed_assignments",
            "episode_counts",
            "transition_counts",
            "sensor_policy_sample_counts",
        },
        "split",
    )
    if (
        split["unit"] != "seed"
        or isinstance(split["split_seed"], bool)
        or not isinstance(split["split_seed"], int)
    ):
        raise DatasetExportError("split configuration is invalid")
    seed_assignments = split["seed_assignments"]
    if not isinstance(seed_assignments, dict):
        raise DatasetExportError("split.seed_assignments must be an object")

    transitions_by_id: dict[str, dict[str, Any]] = {}
    episode_splits: dict[str, str] = {}
    transition_splits: Counter[str] = Counter()
    for record in transitions:
        transition_id = record.get("transition_id")
        episode_id = record.get("episode_id")
        mode = record.get("mode")
        seed = record.get("seed")
        record_split = record.get("split")
        if (
            not isinstance(transition_id, str)
            or not isinstance(episode_id, str)
            or episode_id not in source_ids
            or mode not in included_modes
            or isinstance(seed, bool)
            or not isinstance(seed, int)
            or record_split not in {"train", "validation", "test"}
        ):
            raise DatasetExportError("transition record identity is invalid")
        if transition_id in transitions_by_id:
            raise DatasetExportError(f"duplicate transition_id: {transition_id}")
        if seed_assignments.get(str(seed)) != record_split:
            raise DatasetExportError("transition split does not match seed assignment")
        previous_split = episode_splits.setdefault(episode_id, record_split)
        if previous_split != record_split:
            raise DatasetExportError("one episode appears in multiple splits")
        observation_state = record.get("observation_state")
        outcome_state = record.get("outcome_state")
        action = record.get("action")
        if not isinstance(observation_state, list) or len(observation_state) != len(
            STATE_FEATURES
        ):
            raise DatasetExportError("observation_state feature count is invalid")
        if not isinstance(outcome_state, list) or len(outcome_state) != len(
            STATE_FEATURES
        ):
            raise DatasetExportError("outcome_state feature count is invalid")
        if not isinstance(action, list) or len(action) != len(ACTION_FEATURES):
            raise DatasetExportError("action feature count is invalid")
        transitions_by_id[transition_id] = record
        transition_splits[record_split] += 1

    sensor_splits: Counter[str] = Counter()
    seen_sensor_ids: set[str] = set()
    for record in sensor_records:
        transition_id = record.get("transition_id")
        if not isinstance(transition_id, str) or transition_id in seen_sensor_ids:
            raise DatasetExportError("sensor policy transition_id is invalid")
        if transitions_by_id.get(transition_id) != record:
            raise DatasetExportError(
                "sensor policy record does not match transitions index"
            )
        if not record.get("rgb_path") or not record.get("depth_path"):
            raise DatasetExportError("sensor policy record has no sensor pair")
        seen_sensor_ids.add(transition_id)
        sensor_splits[str(record["split"])] += 1

    episode_count = _require_non_negative_integer(
        manifest["episode_count"], "episode_count"
    )
    if episode_count != len(episode_splits):
        raise DatasetExportError("episode_count does not match indexes")
    excluded_count = _require_non_negative_integer(
        manifest["excluded_episode_count"], "excluded_episode_count"
    )
    if excluded_count != source_count - episode_count:
        raise DatasetExportError("excluded_episode_count does not match source tree")
    episode_split_counts = Counter(episode_splits.values())
    if split["episode_counts"] != dict(sorted(episode_split_counts.items())):
        raise DatasetExportError("split.episode_counts do not match indexes")
    if split["transition_counts"] != dict(sorted(transition_splits.items())):
        raise DatasetExportError("split.transition_counts do not match indexes")
    if split["sensor_policy_sample_counts"] != dict(sorted(sensor_splits.items())):
        raise DatasetExportError(
            "split.sensor_policy_sample_counts do not match indexes"
        )

    _require_exact_keys(
        manifest["causal_alignment"],
        {
            "observation",
            "action",
            "outcome",
            "same_frame_sensor_action_pairing",
        },
        "causal_alignment",
    )
    if manifest["causal_alignment"]["same_frame_sensor_action_pairing"] is not False:
        raise DatasetExportError("causal alignment must prevent same-frame pairing")
    _require_exact_keys(
        manifest["sensor"],
        {"rgb", "depth", "depth_max_cm", "invalid_depth_value"},
        "sensor",
    )
    if manifest["generation_sha256"] != _generation_sha256(manifest):
        raise DatasetExportError("generation_sha256 does not match dataset files")

    return {
        "manifest": manifest,
        "source_root": source_root,
        "transitions": transitions,
        "sensor_records": sensor_records,
    }


def inspect_dataset(dataset_directory: Path | str) -> dict[str, Any]:
    validated = validate_dataset_index(dataset_directory)
    manifest = validated["manifest"]
    source_root = validated["source_root"]
    sensor_records = validated["sensor_records"]
    if not sensor_records:
        raise DatasetExportError("dataset has no sensor policy samples")
    sample = sensor_records[0]

    rgb_path = _resolve_source_path(source_root, str(sample["rgb_path"]))
    depth_path = _resolve_source_path(source_root, str(sample["depth_path"]))
    with Image.open(rgb_path) as rgb_image:
        rgb = np.asarray(rgb_image.convert("RGB"), dtype=np.uint8)
    with Image.open(depth_path) as depth_image:
        depth = np.asarray(depth_image, dtype=np.uint16)
    valid_depth = depth > 0
    decoded_depth = (
        depth.astype(np.float32) / 65535.0 * float(manifest["sensor"]["depth_max_cm"])
    )

    return {
        "transition_id": sample["transition_id"],
        "split": sample["split"],
        "rgb_shape": list(rgb.shape),
        "rgb_dtype": str(rgb.dtype),
        "depth_shape": list(depth.shape),
        "depth_dtype": str(depth.dtype),
        "depth_valid_fraction": float(np.mean(valid_depth)),
        "depth_valid_mean_cm": (
            float(np.mean(decoded_depth[valid_depth])) if np.any(valid_depth) else 0.0
        ),
        "observation_state_features": len(sample["observation_state"]),
        "action_features": len(sample["action"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export validated SimTrace episodes as causal ML indexes"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("episodes_root", type=Path)
    export_parser.add_argument("--output", type=Path)
    export_parser.add_argument("--split-seed", type=int, default=DEFAULT_SPLIT_SEED)
    export_parser.add_argument("--include-input-replay", action="store_true")

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("dataset_directory", type=Path)

    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "export":
            output = arguments.output or (
                arguments.episodes_root.resolve().parent / "ml_dataset"
            )
            result = export_dataset(
                arguments.episodes_root,
                output,
                split_seed=arguments.split_seed,
                include_input_replay=arguments.include_input_replay,
            )
        else:
            result = inspect_dataset(arguments.dataset_directory)
    except (
        DatasetExportError,
        OSError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
