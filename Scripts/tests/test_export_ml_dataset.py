from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from Scripts.export_ml_dataset import (
    DatasetExportError,
    assign_seed_splits,
    export_dataset,
    inspect_dataset,
)
from Scripts.tests.test_validate_dataset import DatasetFixture


class ExportMlDatasetTests(unittest.TestCase):
    def test_seed_split_is_deterministic_and_prevents_leakage(self) -> None:
        first = assign_seed_splits([1000, 1001, 1002, 1003, 1004], 17)
        second = assign_seed_splits([1004, 1002, 1000, 1003, 1001], 17)

        self.assertEqual(first, second)
        self.assertEqual({"train", "validation", "test"}, set(first.values()))
        self.assertEqual(5, len(first))

    def test_export_aligns_previous_observation_with_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = DatasetFixture(root / "episodes", "episode_original")
            output = root / "ml_dataset"

            manifest = export_dataset(root / "episodes", output, split_seed=17)

            transitions = [
                json.loads(line)
                for line in (output / "transitions.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            sensor_samples = [
                json.loads(line)
                for line in (output / "sensor_policy.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]

            self.assertEqual(3, manifest["transition_count"])
            self.assertEqual(1, manifest["sensor_policy_sample_count"])
            self.assertEqual(0, transitions[0]["observation_sim_frame"])
            self.assertEqual(1, transitions[0]["action_sim_frame"])
            self.assertEqual(
                fixture.rows[0]["position_cm"],
                transitions[0]["observation_state"][:3],
            )
            self.assertEqual(fixture.rows[1]["move_input"], transitions[0]["action"][:2])
            self.assertEqual(fixture.rows[1]["position_cm"], transitions[0]["outcome_state"][:3])
            self.assertEqual("episode_original/rgb/000000.png", transitions[0]["rgb_path"])
            self.assertEqual(transitions[0], sensor_samples[0])

    def test_export_assigns_every_episode_with_same_seed_to_one_split(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            episodes = root / "episodes"
            fixtures = [
                DatasetFixture(episodes, "episode_a"),
                DatasetFixture(episodes, "episode_b"),
                DatasetFixture(episodes, "episode_c"),
                DatasetFixture(episodes, "episode_d"),
            ]
            fixtures[0].update_valid_manifest(seed=2000)
            fixtures[1].update_valid_manifest(seed=2000)
            fixtures[2].update_valid_manifest(seed=2001)
            fixtures[3].update_valid_manifest(seed=2002)

            export_dataset(episodes, root / "ml_dataset", split_seed=99)

            rows = [
                json.loads(line)
                for line in (root / "ml_dataset" / "transitions.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            splits_by_seed: dict[int, set[str]] = {}
            for row in rows:
                splits_by_seed.setdefault(row["seed"], set()).add(row["split"])

            self.assertTrue(all(len(splits) == 1 for splits in splits_by_seed.values()))
            self.assertEqual({"train", "validation", "test"}, {row["split"] for row in rows})

    def test_export_excludes_input_replay_duplicates_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            DatasetFixture(root / "episodes", "episode_source")
            replay = DatasetFixture(
                root / "episodes", "episode_replay", mode="input-replay"
            )
            replay.update_valid_manifest(parent_episode_id="episode_source")

            manifest = export_dataset(root / "episodes", root / "ml_dataset")

            self.assertEqual(2, manifest["source_episode_count"])
            self.assertEqual(1, manifest["episode_count"])
            self.assertEqual(1, manifest["excluded_episode_count"])
            self.assertEqual(["bot", "human"], manifest["included_modes"])
            transitions = (root / "ml_dataset" / "transitions.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("episode_replay", transitions)

    def test_inspect_loads_real_rgb_and_depth_arrays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            DatasetFixture(root / "episodes", "episode_original")
            output = root / "ml_dataset"
            export_dataset(root / "episodes", output)

            inspection = inspect_dataset(output)

            self.assertEqual([180, 320, 3], inspection["rgb_shape"])
            self.assertEqual("uint8", inspection["rgb_dtype"])
            self.assertEqual([180, 320], inspection["depth_shape"])
            self.assertEqual("uint16", inspection["depth_dtype"])
            self.assertEqual(12, inspection["observation_state_features"])
            self.assertEqual(6, inspection["action_features"])
            self.assertGreater(inspection["depth_valid_fraction"], 0.0)

    def test_invalid_episode_fails_before_publishing_an_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = DatasetFixture(root / "episodes", "episode_invalid")
            fixture.update_manifest(total_bytes=0)
            output = root / "ml_dataset"

            with self.assertRaisesRegex(DatasetExportError, "episode_invalid"):
                export_dataset(root / "episodes", output)

            self.assertFalse((output / "dataset.json").exists())


if __name__ == "__main__":
    unittest.main()
