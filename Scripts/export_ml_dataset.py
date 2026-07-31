from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

if __package__:
    from .validate_dataset import (
        _load_json as _load_validated_json,
        _load_trajectory,
        validate_episode,
    )
else:
    from validate_dataset import (
        _load_json as _load_validated_json,
        _load_trajectory,
        validate_episode,
    )


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


def _stable_seed_rank(seed: int, split_seed: int) -> bytes:
    return hashlib.sha256(f"{split_seed}:{seed}".encode("utf-8")).digest()


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


def export_dataset(
    episodes_root: Path | str,
    output_directory: Path | str,
    split_seed: int = DEFAULT_SPLIT_SEED,
    include_input_replay: bool = False,
) -> dict[str, Any]:
    episodes_root = Path(episodes_root).resolve()
    output = Path(output_directory).resolve()
    if not episodes_root.is_dir():
        raise DatasetExportError(f"episodes root does not exist: {episodes_root}")

    episode_paths = sorted(
        path
        for path in episodes_root.iterdir()
        if path.is_dir() and (path / "manifest.json").is_file()
    )
    if not episode_paths:
        raise DatasetExportError(f"no complete episodes found in {episodes_root}")

    episodes: list[tuple[Path, dict[str, Any]]] = []
    invalid: list[str] = []
    for episode_path in episode_paths:
        result = validate_episode(episode_path)
        if result["errors"]:
            invalid.append(
                f"{episode_path.name}: " + "; ".join(result["errors"][:3])
            )
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
        episode
        for episode in episodes
        if episode[1].get("mode") in included_modes
    ]
    if not included_episodes:
        raise DatasetExportError("no episodes match the requested training modes")
    excluded_episode_count = source_episode_count - len(included_episodes)

    seed_splits = assign_seed_splits(
        [
            int(manifest["seed"])
            for _, manifest in included_episodes
        ],
        split_seed,
    )
    episode_split_counts: Counter[str] = Counter()
    transition_split_counts: Counter[str] = Counter()
    sensor_split_counts: Counter[str] = Counter()
    engine_versions: set[str] = set()
    git_revisions: set[str] = set()
    transition_count = 0
    sensor_policy_sample_count = 0

    output.mkdir(parents=True, exist_ok=True)
    transitions_partial = output / "transitions.partial.jsonl"
    sensor_partial = output / "sensor_policy.partial.jsonl"
    manifest_partial = output / "dataset.partial.json"
    with transitions_partial.open(
        "w", encoding="utf-8", newline="\n"
    ) as transitions_stream, sensor_partial.open(
        "w", encoding="utf-8", newline="\n"
    ) as sensor_stream:
        for episode_path, manifest in included_episodes:
            episode_id = str(manifest["episode_id"])
            seed = int(manifest["seed"])
            split = seed_splits[seed]
            episode_split_counts[split] += 1
            engine_versions.add(str(manifest["engine_version"]))
            git_revisions.add(str(manifest["git_revision"]))
            rows = _load_trajectory(episode_path / "trajectory.jsonl")

            for observation, outcome in zip(rows, rows[1:]):
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
                serialized = json.dumps(
                    transition,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "\n"
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
        "episode_count": len(included_episodes),
        "excluded_episode_count": excluded_episode_count,
        "included_modes": sorted(included_modes),
        "transition_count": transition_count,
        "sensor_policy_sample_count": sensor_policy_sample_count,
        "state_features": list(STATE_FEATURES),
        "action_features": list(ACTION_FEATURES),
        "split": {
            "unit": "seed",
            "algorithm": "sha256(split_seed:course_seed) rank with 80/10/10 allocation",
            "split_seed": split_seed,
            "seed_assignments": {
                str(seed): split for seed, split in sorted(seed_splits.items())
            },
            "episode_counts": dict(sorted(episode_split_counts.items())),
            "transition_counts": dict(sorted(transition_split_counts.items())),
            "sensor_policy_sample_counts": dict(sorted(sensor_split_counts.items())),
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
        "complete": True,
    }

    manifest_partial.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    transitions_partial.replace(output / "transitions.jsonl")
    sensor_partial.replace(output / "sensor_policy.jsonl")
    manifest_partial.replace(output / "dataset.json")
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


def inspect_dataset(dataset_directory: Path | str) -> dict[str, Any]:
    dataset_directory = Path(dataset_directory).resolve()
    manifest = _load_json(dataset_directory / "dataset.json")
    source_reference = manifest.get("source_episodes_root")
    if not isinstance(source_reference, str) or not source_reference:
        raise DatasetExportError("dataset source_episodes_root is missing")
    source_root = (dataset_directory / Path(source_reference)).resolve()

    sensor_index = dataset_directory / str(manifest["files"]["sensor_policy"])
    with sensor_index.open("r", encoding="utf-8") as stream:
        first_line = next((line for line in stream if line.strip()), None)
    if first_line is None:
        raise DatasetExportError("dataset has no sensor policy samples")
    sample = json.loads(first_line)
    if not isinstance(sample, dict):
        raise DatasetExportError("sensor policy sample must be a JSON object")

    rgb_path = _resolve_source_path(source_root, str(sample["rgb_path"]))
    depth_path = _resolve_source_path(source_root, str(sample["depth_path"]))
    with Image.open(rgb_path) as rgb_image:
        rgb = np.asarray(rgb_image.convert("RGB"), dtype=np.uint8)
    with Image.open(depth_path) as depth_image:
        depth = np.asarray(depth_image, dtype=np.uint16)
    valid_depth = depth > 0
    decoded_depth = depth.astype(np.float32) / 65535.0 * float(
        manifest["sensor"]["depth_max_cm"]
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
            float(np.mean(decoded_depth[valid_depth]))
            if np.any(valid_depth)
            else 0.0
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
    export_parser.add_argument(
        "--split-seed", type=int, default=DEFAULT_SPLIT_SEED
    )
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
    except (DatasetExportError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
