from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from Scripts.validate_dataset import compare_episodes, validate_episode


class DatasetFixture:
    def __init__(self, root: Path, episode_id: str, mode: str = "bot") -> None:
        self.path = root / episode_id
        (self.path / "rgb").mkdir(parents=True)
        (self.path / "depth").mkdir()
        (self.path / "replay").mkdir()

        self.rows = []
        for frame in range(4):
            captured = frame in {0, 3}
            row = {
                "schema_version": 1,
                "sim_frame": frame,
                "timestamp_s": frame / 30,
                "delta_s": 1 / 30,
                "position_cm": [frame * 10.0, 0.0, 96.0],
                "rotation_deg": [0.0, 0.0, 0.0],
                "velocity_cm_s": [300.0, 0.0, 0.0],
                "goal_relative_cm": [100.0, 0.0, 0.0],
                "move_input": [0.0, 1.0],
                "look_input": [0.0, 0.0],
                "jump_pressed": False,
                "collision": False,
                "captured": captured,
                "capture_dropped": False,
                "rgb_path": f"rgb/{frame:06d}.png" if captured else None,
                "depth_path": f"depth/{frame:06d}.png" if captured else None,
                "frame_time_ms": 33.3,
                "done": frame == 3,
                "end_reason": "goal" if frame == 3 else "",
            }
            self.rows.append(row)

        with (self.path / "trajectory.jsonl").open("w", encoding="utf-8") as stream:
            for row in self.rows:
                stream.write(json.dumps(row) + "\n")

        for frame in (0, 3):
            Image.new("RGBA", (320, 180), (32, 64, 96, 255)).save(
                self.path / "rgb" / f"{frame:06d}.png"
            )
            Image.new("I;16", (320, 180), 32768).save(
                self.path / "depth" / f"{frame:06d}.png"
            )

        replay_name = f"simtrace_{episode_id}"
        (self.path / "replay" / f"{replay_name}.replay").write_bytes(b"replay")
        manifest = {
            "schema_version": 1,
            "episode_id": episode_id,
            "mode": mode,
            "seed": 1000,
            "parent_episode_id": "",
            "course_hash": "abc123",
            "start_position_cm": [100.0, 0.0, 96.0],
            "goal_position_cm": [3100.0, 0.0, 100.0],
            "simulation_hz": 30,
            "capture_hz": 10,
            "image_width": 320,
            "image_height": 180,
            "depth_encoding": "uint16_linear_cm",
            "depth_max_cm": 2000,
            "trajectory_frames": 4,
            "capture_frames": 2,
            "capture_dropped": 0,
            "end_reason": "goal",
            "replay_name": replay_name,
            "replay_archive_path": f"replay/{replay_name}.replay",
            "complete": True,
        }
        (self.path / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )


class ValidateDatasetTest(unittest.TestCase):
    def test_valid_episode_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DatasetFixture(Path(directory), "episode_original")
            result = validate_episode(fixture.path)
            self.assertEqual([], result["errors"])
            self.assertEqual(4, result["trajectory_frames"])
            self.assertEqual(2, result["capture_frames"])

    def test_missing_depth_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DatasetFixture(Path(directory), "episode_missing")
            (fixture.path / "depth" / "000003.png").unlink()
            result = validate_episode(fixture.path)
            self.assertTrue(
                any("depth/000003.png" in error for error in result["errors"])
            )

    def test_identical_replay_has_zero_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = DatasetFixture(root, "episode_original")
            replayed = DatasetFixture(root, "episode_replayed", mode="input-replay")
            metrics = compare_episodes(original.path, replayed.path)
            self.assertEqual(0.0, metrics["mean_position_error_cm"])
            self.assertEqual(0.0, metrics["final_position_error_cm"])
            self.assertTrue(metrics["within_target"])


if __name__ == "__main__":
    unittest.main()

