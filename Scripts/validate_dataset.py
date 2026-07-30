from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


POSITION_TARGETS_CM = {
    "mean": 10.0,
    "p95": 25.0,
    "final": 50.0,
    "start": 0.1,
}


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
    header = path.read_bytes()[:26]
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
    return [float(item) for item in value]


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
        "trajectory_frames": 0,
        "capture_frames": 0,
        "capture_dropped": 0,
        "missing_capture_frames": 0,
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
        }
    )
    if not result["complete"]:
        errors.append("manifest complete flag is false")
    if manifest.get("schema_version") != 1:
        errors.append("unsupported manifest schema_version")

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
    simulation_hz = float(manifest.get("simulation_hz", 30))
    capture_hz = float(manifest.get("capture_hz", 0))
    expected_rgb: set[str] = set()
    expected_depth: set[str] = set()
    captured_count = 0
    dropped_count = 0
    missing_capture_count = 0

    for expected_frame, row in enumerate(rows):
        frame = row.get("sim_frame")
        if frame != expected_frame:
            errors.append(
                f"trajectory frame {expected_frame} has sim_frame={frame}"
            )
        timestamp = row.get("timestamp_s")
        expected_timestamp = expected_frame / simulation_hz
        if not isinstance(timestamp, (int, float)) or not math.isclose(
            float(timestamp), expected_timestamp, abs_tol=1e-5
        ):
            errors.append(
                f"frame {expected_frame} timestamp mismatch: "
                f"{timestamp} != {expected_timestamp}"
            )

        captured = bool(row.get("captured", False))
        dropped = bool(row.get("capture_dropped", False))
        if captured and dropped:
            errors.append(f"frame {expected_frame} is both captured and dropped")
        if captured:
            captured_count += 1
            rgb_relative = row.get("rgb_path")
            depth_relative = row.get("depth_path")
            if not isinstance(rgb_relative, str) or not isinstance(
                depth_relative, str
            ):
                errors.append(
                    f"frame {expected_frame} captured without both image paths"
                )
            else:
                expected_rgb.add(rgb_relative.replace("\\", "/"))
                expected_depth.add(depth_relative.replace("\\", "/"))
                for relative in (rgb_relative, depth_relative):
                    image_path = episode / relative
                    if not image_path.exists():
                        errors.append(f"missing image: {relative}")
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
        except (TypeError, ValueError) as error:
            errors.append(f"frame {expected_frame}: {error}")
            continue

        result["_move_magnitudes"].append(math.hypot(*move))
        result["_look_magnitudes"].append(math.hypot(*look))
        result["_jump_values"].append(bool(row.get("jump_pressed", False)))
        result["_collision_values"].append(bool(row.get("collision", False)))
        frame_time = row.get("frame_time_ms")
        if isinstance(frame_time, (int, float)) and float(frame_time) >= 0:
            result["_frame_times_ms"].append(float(frame_time))

    result["capture_frames"] = captured_count
    result["capture_dropped"] = dropped_count
    result["missing_capture_frames"] = missing_capture_count

    if not rows:
        errors.append("trajectory is empty")
    else:
        if rows[-1].get("done") is not True:
            errors.append("last trajectory row must have done=true")
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
    if dropped_count:
        errors.append(f"episode contains {dropped_count} dropped captures")

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

    width = int(manifest.get("image_width", 320))
    height = int(manifest.get("image_height", 180))
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
    if "file_count" in manifest and manifest["file_count"] != len(files):
        errors.append("manifest file_count does not match files on disk")
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
    course_hash_match = (
        original_manifest.get("course_hash")
        == replayed_manifest.get("course_hash")
    )
    within_target = (
        seed_match
        and course_hash_match
        and start_error <= POSITION_TARGETS_CM["start"]
        and mean_error <= POSITION_TARGETS_CM["mean"]
        and p95_error <= POSITION_TARGETS_CM["p95"]
        and final_error <= POSITION_TARGETS_CM["final"]
    )
    return {
        "original_episode_id": original_manifest.get(
            "episode_id", original_episode.name
        ),
        "replayed_episode_id": replayed_manifest.get(
            "episode_id", replayed_episode.name
        ),
        "shared_frames": len(shared_frames),
        "seed_match": seed_match,
        "course_hash_match": course_hash_match,
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


def _write_plots(
    output: Path,
    results: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> list[str]:
    plot_paths: list[str] = []
    episode_labels = [result["episode_id"] for result in results]
    sizes_mb = [result["total_bytes"] / (1024 * 1024) for result in results]

    plt.figure(figsize=(max(8, len(results) * 0.4), 4.5))
    plt.bar(range(len(results)), sizes_mb, color="#277da1")
    plt.xticks(range(len(results)), episode_labels, rotation=75, ha="right", fontsize=7)
    plt.ylabel("MiB")
    plt.title("Episode sizes")
    plt.tight_layout()
    path = output / "episode_sizes.png"
    plt.savefig(path, dpi=150)
    plt.close()
    plot_paths.append(path.name)

    outcomes = Counter(result["end_reason"] for result in results)
    plt.figure(figsize=(7, 4))
    plt.bar(outcomes.keys(), outcomes.values(), color="#43aa8b")
    plt.ylabel("Episodes")
    plt.title("Episode outcomes")
    plt.tight_layout()
    path = output / "episode_outcomes.png"
    plt.savefig(path, dpi=150)
    plt.close()
    plot_paths.append(path.name)

    plt.figure(figsize=(8, 4.5))
    plotted = False
    for mode in ("human", "bot", "input-replay"):
        values = [
            value
            for result in results
            if result["mode"] == mode
            for value in result["_move_magnitudes"]
        ]
        if values:
            plt.hist(values, bins=20, alpha=0.45, label=mode)
            plotted = True
    if plotted:
        plt.legend()
    plt.xlabel("Move input magnitude")
    plt.ylabel("Samples")
    plt.title("Action distributions")
    plt.tight_layout()
    path = output / "action_distributions.png"
    plt.savefig(path, dpi=150)
    plt.close()
    plot_paths.append(path.name)

    plt.figure(figsize=(8, 4.5))
    if comparisons:
        labels = [item["replayed_episode_id"] for item in comparisons]
        x = np.arange(len(labels))
        plt.bar(
            x - 0.2,
            [item["mean_position_error_cm"] for item in comparisons],
            width=0.4,
            label="mean",
        )
        plt.bar(
            x + 0.2,
            [item["p95_position_error_cm"] for item in comparisons],
            width=0.4,
            label="p95",
        )
        plt.xticks(x, labels, rotation=60, ha="right", fontsize=7)
        plt.legend()
    plt.ylabel("Position error cm")
    plt.title("JSON input replay error")
    plt.tight_layout()
    path = output / "replay_error.png"
    plt.savefig(path, dpi=150)
    plt.close()
    plot_paths.append(path.name)

    capture_on = [
        value
        for result in results
        if result.get("_capture_hz", 0) > 0
        for value in result["_frame_times_ms"]
    ]
    capture_off = [
        value
        for result in results
        if result.get("_capture_hz", 0) == 0
        for value in result["_frame_times_ms"]
    ]
    plt.figure(figsize=(7, 4.5))
    data: list[list[float]] = []
    labels: list[str] = []
    if capture_off:
        data.append(capture_off)
        labels.append("capture off")
    if capture_on:
        data.append(capture_on)
        labels.append("capture on")
    if data:
        plt.boxplot(data, tick_labels=labels, showfliers=False)
    plt.ylabel("Frame time ms")
    plt.title("Capture performance")
    plt.tight_layout()
    path = output / "capture_performance.png"
    plt.savefig(path, dpi=150)
    plt.close()
    plot_paths.append(path.name)
    return plot_paths


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
            manifest = _load_json(manifest_path)
            manifests[result["episode_id"]] = manifest
            paths_by_id[result["episode_id"]] = path
            result["_capture_hz"] = float(manifest.get("capture_hz", 0))
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
            comparisons.append(compare_episodes(parent_path, replayed_path))
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
        action_metrics[mode] = {
            "move_magnitude": _distribution(moves),
            "look_magnitude": _distribution(looks),
            "jump_rate": statistics.fmean(jumps) if jumps else 0.0,
            "collision_rate": statistics.fmean(collisions)
            if collisions
            else 0.0,
        }

    capture_on = [
        value
        for result in results
        if result["_capture_hz"] > 0
        for value in result["_frame_times_ms"]
    ]
    capture_off = [
        value
        for result in results
        if result["_capture_hz"] == 0
        for value in result["_frame_times_ms"]
    ]
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

    plot_files = _write_plots(output, results, comparisons)
    public_results = []
    for result in results:
        public_results.append(
            {key: value for key, value in result.items() if not key.startswith("_")}
        )

    summary = {
        "schema_version": 1,
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
        "replay_comparisons": comparisons,
        "action_metrics": action_metrics,
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
            "## JSON replay comparisons",
            "",
            "| Replay | Frames | Mean cm | P95 cm | Final cm | Target |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for comparison in summary["replay_comparisons"]:
        lines.append(
            f"| {comparison['replayed_episode_id']} | "
            f"{comparison['shared_frames']} | "
            f"{comparison['mean_position_error_cm']:.3f} | "
            f"{comparison['p95_position_error_cm']:.3f} | "
            f"{comparison['final_position_error_cm']:.3f} | "
            f"{'pass' if comparison['within_target'] else 'fail'} |"
        )

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

