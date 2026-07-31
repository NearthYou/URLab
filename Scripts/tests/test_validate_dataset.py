from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from Scripts.validate_dataset import build_report, compare_episodes, validate_episode


class DatasetFixture:
    def __init__(
        self,
        root: Path,
        episode_id: str,
        mode: str = "bot",
        schema_version: int = 1,
    ) -> None:
        self.path = root / episode_id
        (self.path / "rgb").mkdir(parents=True)
        (self.path / "depth").mkdir()
        (self.path / "replay").mkdir()

        self.rows = []
        for frame in range(4):
            captured = frame in {0, 3}
            row = {
                "schema_version": schema_version,
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
            if schema_version == 2:
                row["fire_pressed"] = frame == 2
                row["combat_events"] = (
                    [
                        {"sequence": 0, "event": "fire", "shot_id": 0},
                        {
                            "sequence": 1,
                            "event": "shot",
                            "shot_id": 0,
                            "origin_cm": [20.0, 0.0, 160.0],
                            "direction": [1.0, 0.0, 0.0],
                        },
                        {
                            "sequence": 2,
                            "event": "hit",
                            "shot_id": 0,
                            "target_id": "target_alpha",
                            "impact_position_cm": [2940.0, 0.0, 160.0],
                            "distance_cm": 2920.0,
                        },
                    ]
                    if frame == 2
                    else []
                )
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
            "schema_version": schema_version,
            "episode_id": episode_id,
            "mode": mode,
            "seed": 1000,
            "parent_episode_id": "",
            "course_hash": "abc123",
            "start_position_cm": [100.0, 0.0, 96.0],
            "start_rotation_deg": [0.0, 0.0, 0.0],
            "goal_position_cm": [3100.0, 0.0, 100.0],
            "engine_version": "5.8.1-test",
            "git_revision": "0123456789ab",
            "simulation_hz": 30,
            "capture_hz": 10,
            "capture_interval_sim_frames": 3,
            "capture_queue_capacity": 8,
            "image_width": 320,
            "image_height": 180,
            "sensor_type": "scene_depth",
            "depth_encoding": "uint16_linear_cm",
            "depth_max_cm": 2000,
            "depth_decode_cm": (
                "value == 0 ? invalid : "
                "min(value / 65535.0 * 2000.0, 2000.0)"
            ),
            "trajectory_frames": 4,
            "capture_frames": 2,
            "capture_dropped": 0,
            "file_count": 7,
            "total_bytes": 0,
            "started_utc": "2026-07-30T00:00:00.000Z",
            "duration_s": 4 / 30,
            "end_reason": "goal",
            "replay_name": replay_name,
            "replay_archive_path": f"replay/{replay_name}.replay",
            "complete": True,
        }
        if schema_version == 2:
            manifest.update(
                {
                    "target_position_cm": [2940.0, 0.0, 170.0],
                    "combat_contract": "one_bullet_outcome_ledger_v1",
                    "primary_target_id": "target_alpha",
                    "shots_fired": 1,
                    "shots_hit": 1,
                    "shot_hit_rate": 1.0,
                }
            )
        (self.path / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        for _ in range(4):
            total_bytes = sum(
                path.stat().st_size for path in self.path.rglob("*") if path.is_file()
            )
            if manifest["total_bytes"] == total_bytes:
                break
            manifest["total_bytes"] = total_bytes
            (self.path / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

    def update_manifest(self, **fields: object) -> None:
        manifest_path = self.path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(fields)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


class ValidateDatasetTest(unittest.TestCase):
    def test_valid_episode_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DatasetFixture(Path(directory), "episode_original")
            result = validate_episode(fixture.path)
            self.assertEqual([], result["errors"])
            self.assertEqual(4, result["trajectory_frames"])
            self.assertEqual(2, result["capture_frames"])

    def test_schema_two_combat_ledger_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DatasetFixture(
                Path(directory), "episode_combat", schema_version=2
            )

            result = validate_episode(fixture.path)

            self.assertEqual([], result["errors"])
            self.assertEqual(1, result["shots_fired"])
            self.assertEqual(1, result["shots_hit"])
            self.assertEqual(1.0, result["shot_hit_rate"])

    def test_combat_ledger_rejects_broken_event_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DatasetFixture(
                Path(directory), "episode_broken_ledger", schema_version=2
            )
            events = fixture.rows[2]["combat_events"]
            events[0], events[1] = events[1], events[0]
            (fixture.path / "trajectory.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in fixture.rows),
                encoding="utf-8",
            )

            result = validate_episode(fixture.path)

            self.assertTrue(
                any("combat event order" in error for error in result["errors"])
            )

    def test_combat_ledger_rejects_non_string_event_without_crashing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DatasetFixture(
                Path(directory), "episode_bad_event_type", schema_version=2
            )
            fixture.rows[2]["combat_events"][2]["event"] = ["hit"]
            (fixture.path / "trajectory.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in fixture.rows),
                encoding="utf-8",
            )

            result = validate_episode(fixture.path)

            self.assertTrue(
                any("combat event order" in error for error in result["errors"])
            )

    def test_missing_depth_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DatasetFixture(Path(directory), "episode_missing")
            (fixture.path / "depth" / "000003.png").unlink()
            result = validate_episode(fixture.path)
            self.assertTrue(
                any("depth/000003.png" in error for error in result["errors"])
            )

    def test_depth_path_cannot_alias_rgb_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DatasetFixture(Path(directory), "episode_aliased_depth")
            fixture.rows[3]["depth_path"] = "rgb/000003.png"
            (fixture.path / "trajectory.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in fixture.rows),
                encoding="utf-8",
            )

            result = validate_episode(fixture.path)

            self.assertTrue(
                any("depth_path mismatch" in error for error in result["errors"])
            )

    def test_non_final_done_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DatasetFixture(Path(directory), "episode_early_done")
            fixture.rows[1]["done"] = True
            fixture.rows[1]["end_reason"] = "goal"
            (fixture.path / "trajectory.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in fixture.rows),
                encoding="utf-8",
            )

            result = validate_episode(fixture.path)

            self.assertTrue(
                any("done=true before the end" in error for error in result["errors"])
            )

    def test_identical_replay_has_zero_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = DatasetFixture(root, "episode_original")
            replayed = DatasetFixture(root, "episode_replayed", mode="input-replay")
            replayed.update_manifest(parent_episode_id="episode_original")
            metrics = compare_episodes(original.path, replayed.path)
            self.assertEqual(0.0, metrics["mean_position_error_cm"])
            self.assertEqual(0.0, metrics["final_position_error_cm"])
            self.assertTrue(metrics["parent_episode_match"])
            self.assertTrue(metrics["frame_alignment_match"])
            self.assertFalse(metrics["combat_event_applicable"])
            self.assertTrue(metrics["within_target"])

    def test_replay_requires_exact_combat_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = DatasetFixture(
                root, "episode_original_combat", schema_version=2
            )
            replayed = DatasetFixture(
                root,
                "episode_replayed_combat",
                mode="input-replay",
                schema_version=2,
            )
            replayed.update_manifest(parent_episode_id="episode_original_combat")
            replayed.rows[2]["combat_events"][2]["target_id"] = "target_bravo"
            (replayed.path / "trajectory.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in replayed.rows),
                encoding="utf-8",
            )

            metrics = compare_episodes(original.path, replayed.path)

            self.assertFalse(metrics["combat_event_match"])
            self.assertEqual([2], metrics["combat_mismatch_frames"])
            self.assertFalse(metrics["within_target"])

    def test_report_includes_combat_summary_and_plot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            DatasetFixture(root, "episode_combat", schema_version=2)

            summary = build_report(root, root / "reports")

            self.assertEqual(1, summary["combat"]["shots_fired"])
            self.assertEqual(1, summary["combat"]["shots_hit"])
            self.assertEqual(1.0, summary["combat"]["shot_hit_rate"])
            self.assertIn("combat_ledger.png", summary["plots"])
            self.assertTrue((root / "reports" / "combat_ledger.png").is_file())

    def test_replay_with_wrong_parent_fails_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = DatasetFixture(root, "episode_original")
            replayed = DatasetFixture(root, "episode_replayed", mode="input-replay")

            metrics = compare_episodes(original.path, replayed.path)

            self.assertFalse(metrics["parent_episode_match"])
            self.assertFalse(metrics["within_target"])

    def test_replay_with_missing_frame_fails_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = DatasetFixture(root, "episode_original")
            replayed = DatasetFixture(root, "episode_replayed", mode="input-replay")
            replayed.update_manifest(parent_episode_id="episode_original")
            (replayed.path / "trajectory.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in replayed.rows[:-1]),
                encoding="utf-8",
            )

            metrics = compare_episodes(original.path, replayed.path)

            self.assertFalse(metrics["frame_alignment_match"])
            self.assertFalse(metrics["within_target"])

    def test_replay_missing_shot_frame_fails_exact_combat_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = DatasetFixture(
                root, "episode_original_combat", schema_version=2
            )
            replayed = DatasetFixture(
                root,
                "episode_replayed_combat",
                mode="input-replay",
                schema_version=2,
            )
            replayed.update_manifest(
                parent_episode_id="episode_original_combat"
            )
            without_shot = [
                row for row in replayed.rows if row["sim_frame"] != 2
            ]
            (replayed.path / "trajectory.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in without_shot),
                encoding="utf-8",
            )

            metrics = compare_episodes(original.path, replayed.path)

            self.assertTrue(metrics["combat_event_applicable"])
            self.assertFalse(metrics["combat_event_match"])
            self.assertEqual([2], metrics["combat_mismatch_frames"])
            self.assertFalse(metrics["within_target"])

    def test_unavailable_git_revision_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DatasetFixture(Path(directory), "episode_no_revision")
            fixture.update_manifest(git_revision="unavailable")

            result = validate_episode(fixture.path)

            self.assertIn("manifest git_revision is unavailable", result["errors"])

    def test_manifest_total_bytes_must_match_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = DatasetFixture(Path(directory), "episode_wrong_bytes")
            fixture.update_manifest(total_bytes=0)

            result = validate_episode(fixture.path)

            self.assertIn(
                "manifest total_bytes does not match files on disk",
                result["errors"],
            )

    def test_invalid_manifest_types_are_reported_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = DatasetFixture(root, "episode_wrong_types")
            fixture.update_manifest(
                simulation_hz="thirty",
                capture_hz=[],
                git_revision=[],
                total_bytes="unknown",
            )

            result = validate_episode(fixture.path)
            summary = build_report(root, root / "reports")

            self.assertTrue(result["errors"])
            self.assertGreater(summary["error_count"], 0)

    def test_report_handles_constant_action_magnitude(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = DatasetFixture(root, "episode_constant")
            for frame, row in enumerate(fixture.rows):
                row["move_input"] = [0.0, 1.0 + frame * 1e-16]
            (fixture.path / "trajectory.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in fixture.rows),
                encoding="utf-8",
            )

            summary = build_report(root, root / "reports")

            self.assertEqual(1, summary["episode_count"])
            self.assertTrue((root / "reports" / "action_distributions.png").is_file())
            markdown = (root / "reports" / "summary.md").read_text(encoding="utf-8")
            self.assertIn("## Capture performance", markdown)
            self.assertIn("## Seed reproducibility", markdown)

    def test_performance_report_uses_only_bot_capture_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            DatasetFixture(root, "episode_capture_on")
            capture_off = DatasetFixture(root, "episode_capture_off")
            capture_off.update_manifest(capture_hz=0)
            replay = DatasetFixture(root, "episode_replay", mode="input-replay")
            replay.update_manifest(capture_hz=0)

            summary = build_report(root, root / "reports")

            self.assertEqual(4, summary["performance"]["capture_on_ms"]["count"])
            self.assertEqual(4, summary["performance"]["capture_off_ms"]["count"])

    def test_report_keeps_invalid_manifest_as_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = DatasetFixture(root, "episode_invalid_manifest")
            (fixture.path / "manifest.json").write_text("{", encoding="utf-8")

            summary = build_report(root, root / "reports")

            self.assertEqual(1, summary["episode_count"])
            self.assertGreater(summary["error_count"], 0)
            self.assertTrue((root / "reports" / "summary.json").is_file())

    def test_report_keeps_invalid_replay_as_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            DatasetFixture(root, "episode_original")
            replayed = DatasetFixture(
                root, "episode_invalid_replay", mode="input-replay"
            )
            replayed.update_manifest(parent_episode_id="episode_original")
            (replayed.path / "trajectory.jsonl").write_text(
                "not-json\n", encoding="utf-8"
            )

            summary = build_report(root, root / "reports")

            replay_result = next(
                episode
                for episode in summary["episodes"]
                if episode["episode_id"] == "episode_invalid_replay"
            )
            self.assertTrue(
                any(
                    "replay comparison failed" in error
                    for error in replay_result["errors"]
                )
            )


if __name__ == "__main__":
    unittest.main()
