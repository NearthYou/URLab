from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from Scripts.export_ml_dataset import (
    ACTION_FEATURES,
    STATE_FEATURES,
    _generation_sha256,
    source_manifest_set_sha256,
)
from Scripts.publish_evidence import (
    _create_video,
    _stream_video_frames,
    publish_evidence,
)
from Scripts.validate_dataset import benchmark_registry_sha256


class PublishEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.episodes_root = self.root / "episodes"
        self.reports_root = self.root / "reports"
        self.output_root = self.root / "evidence"
        self.episodes_root.mkdir()
        self.reports_root.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write_episode(
        self,
        episode_id: str,
        *,
        mode: str,
        seed: int,
        capture_hz: int,
        end_reason: str,
    ) -> Path:
        episode = self.episodes_root / episode_id
        (episode / "rgb").mkdir(parents=True)
        (episode / "depth").mkdir()
        manifest = {
            "schema_version": 1,
            "episode_id": episode_id,
            "mode": mode,
            "seed": seed,
            "capture_hz": capture_hz,
            "capture_frames": 2 if capture_hz else 0,
            "capture_dropped": 0,
            "trajectory_frames": 3,
            "end_reason": end_reason,
            "complete": True,
            "engine_version": "5.8.1-test",
            "git_revision": "abc123def456",
            "course_hash": f"hash-{seed}",
        }
        (episode / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        rows = [
            {
                "sim_frame": 0,
                "captured": capture_hz > 0,
                "rgb_path": "rgb/000000.png" if capture_hz else None,
                "depth_path": "depth/000000.png" if capture_hz else None,
                "done": False,
            },
            {
                "sim_frame": 1,
                "captured": False,
                "rgb_path": None,
                "depth_path": None,
                "done": False,
            },
            {
                "sim_frame": 3,
                "captured": capture_hz > 0,
                "rgb_path": "rgb/000003.png" if capture_hz else None,
                "depth_path": "depth/000003.png" if capture_hz else None,
                "done": True,
            },
        ]
        (episode / "trajectory.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        if capture_hz:
            for frame, color in ((0, (20, 40, 60)), (3, (80, 100, 120))):
                Image.new("RGB", (320, 180), color).save(
                    episode / "rgb" / f"{frame:06d}.png"
                )
                depth = np.full((180, 320), 12000 + frame, dtype=np.uint16)
                Image.fromarray(depth).save(episode / "depth" / f"{frame:06d}.png")
        return episode

    def _write_ml_dataset(self) -> None:
        output = self.root / "ml_dataset"
        output.mkdir()
        manifests = [
            json.loads((episode / "manifest.json").read_text(encoding="utf-8"))
            for episode in sorted(self.episodes_root.iterdir())
        ]
        included = [
            manifest for manifest in manifests if manifest["mode"] in {"bot", "human"}
        ]
        transitions = []
        seed_assignments: dict[str, str] = {}
        for manifest in included:
            seed_assignments[str(manifest["seed"])] = "train"
            transitions.append(
                {
                    "schema_version": 1,
                    "transition_id": f"{manifest['episode_id']}:000001",
                    "split": "train",
                    "episode_id": manifest["episode_id"],
                    "mode": manifest["mode"],
                    "seed": manifest["seed"],
                    "course_hash": manifest["course_hash"],
                    "observation_sim_frame": 0,
                    "action_sim_frame": 1,
                    "outcome_sim_frame": 1,
                    "observation_state": [0.0] * len(STATE_FEATURES),
                    "action": [0.0] * len(ACTION_FEATURES),
                    "outcome_state": [0.0] * len(STATE_FEATURES),
                    "rgb_path": f"{manifest['episode_id']}/rgb/000000.png",
                    "depth_path": f"{manifest['episode_id']}/depth/000000.png",
                    "collision": False,
                    "combat_events": [],
                    "done": False,
                    "end_reason": "",
                }
            )
        serialized = "".join(
            json.dumps(record, separators=(",", ":")) + "\n" for record in transitions
        )
        transitions_path = output / "transitions.jsonl"
        sensor_path = output / "sensor_policy.jsonl"
        transitions_path.write_text(serialized, encoding="utf-8")
        sensor_path.write_text(serialized, encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "dataset_type": "simtrace_causal_transition_index",
            "source_episodes_root": "../episodes",
            "source_episode_count": len(manifests),
            "source_manifest_set_sha256": source_manifest_set_sha256(
                self.episodes_root
            ),
            "episode_count": len(included),
            "excluded_episode_count": len(manifests) - len(included),
            "included_modes": ["bot", "human"],
            "transition_count": len(transitions),
            "sensor_policy_sample_count": len(transitions),
            "state_features": list(STATE_FEATURES),
            "action_features": list(ACTION_FEATURES),
            "split": {
                "unit": "seed",
                "algorithm": "test seed split",
                "split_seed": 17,
                "seed_assignments": seed_assignments,
                "episode_counts": {"train": len(included)},
                "transition_counts": {"train": len(transitions)},
                "sensor_policy_sample_counts": {"train": len(transitions)},
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
            "source_engine_versions": ["5.8.1-test"],
            "source_git_revisions": ["abc123def456"],
            "files": {
                "transitions": "transitions.jsonl",
                "sensor_policy": "sensor_policy.jsonl",
            },
            "file_integrity": {
                "transitions": {
                    "sha256": hashlib.sha256(transitions_path.read_bytes()).hexdigest(),
                    "record_count": len(transitions),
                },
                "sensor_policy": {
                    "sha256": hashlib.sha256(sensor_path.read_bytes()).hexdigest(),
                    "record_count": len(transitions),
                },
            },
            "complete": True,
        }
        manifest["generation_sha256"] = _generation_sha256(manifest)
        (output / "dataset.json").write_text(json.dumps(manifest), encoding="utf-8")

    @staticmethod
    def _public_pubg_summary() -> dict[str, object]:
        zero_hash = "0" * 64
        return {
            "schema_version": 1,
            "dataset": "pubg_telemetry_aggregate",
            "match": {
                "platform": "steam",
                "match_id_sha256": zero_hash,
                "map_name": "Test_Main",
                "team_size": 4,
                "event_count": 0,
                "first_event_utc": None,
                "last_event_utc": None,
            },
            "event_type_counts": {},
            "combat": {
                "attacks": 0,
                "damage_events": 0,
                "knock_events": 0,
                "kill_events": 0,
                "attacks_with_damage": 0,
                "attacks_with_knock": 0,
                "attacks_with_kill": 0,
                "unmatched_damage_attack_ids": 0,
                "total_damage": 0,
                "engagement_distance_raw": {
                    "count": 0,
                    "mean": 0,
                    "p50": 0,
                    "p95": 0,
                },
            },
            "weapons": {},
            "position_samples": 0,
            "phase_changes": 0,
            "provenance": {
                "source": "PUBG Developer API telemetry",
                "retrieved_utc": "2026-07-31T00:00:00Z",
                "telemetry_host": "telemetry-cdn.pubg.com",
                "source_url_sha256": zero_hash,
                "match_id_sha256": zero_hash,
                "raw_sha256": zero_hash,
                "raw_data_publishable": False,
                "contains_player_identifiers": False,
                "telemetry_docs": "https://documentation.pubg.com/en/telemetry.html",
                "event_docs": "https://documentation.pubg.com/en/telemetry-events.html",
                "terms": "https://developer.pubg.com/tos?locale=en",
            },
        }

    def _write_report(self) -> None:
        episodes = []
        modes: dict[str, int] = {}
        outcomes: dict[str, int] = {}
        for episode in sorted(self.episodes_root.iterdir()):
            manifest = json.loads(
                (episode / "manifest.json").read_text(encoding="utf-8")
            )
            episodes.append(
                {
                    "path": str(episode),
                    "episode_id": manifest["episode_id"],
                    **{
                        field: manifest[field]
                        for field in (
                            "mode",
                            "seed",
                            "course_hash",
                            "end_reason",
                            "complete",
                            "trajectory_frames",
                            "capture_frames",
                            "capture_dropped",
                        )
                    },
                }
            )
            modes[manifest["mode"]] = modes.get(manifest["mode"], 0) + 1
            outcomes[manifest["end_reason"]] = (
                outcomes.get(manifest["end_reason"], 0) + 1
            )
        summary = {
            "schema_version": 1,
            "episode_count": len(episodes),
            "valid_episode_count": len(episodes),
            "error_count": 0,
            "warning_count": 0,
            "total_bytes": 123456,
            "missing_capture_frames": 0,
            "capture_dropped": 0,
            "modes": modes,
            "outcomes": outcomes,
            "episodes": episodes,
            "replay_comparisons": [
                {
                    "seed": 1000,
                    "mean_position_error_cm": 0.0,
                    "p95_position_error_cm": 0.0,
                    "max_position_error_cm": 0.0,
                    "final_position_error_cm": 0.0,
                    "within_target": True,
                }
            ],
            "performance": {
                "capture_on_ms": {"count": 10, "p50": 35.0},
                "capture_off_ms": {"count": 10, "p50": 34.0},
                "median_fps_drop_percent": 2.857,
                "registered_benchmark": {
                    "design_errors": [],
                    "registry_sha256": benchmark_registry_sha256(self.episodes_root),
                },
            },
            "plots": [
                "episode_sizes.png",
                "episode_outcomes.png",
                "action_distributions.png",
                "combat_ledger.png",
                "replay_error.png",
                "capture_performance.png",
            ],
        }
        (self.reports_root / "summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        (self.reports_root / "summary.md").write_text(
            "# Test summary\n", encoding="utf-8"
        )
        for plot in summary["plots"]:
            Image.new("RGB", (640, 360), (240, 240, 240)).save(self.reports_root / plot)

    def test_publishes_real_samples_and_report_artifacts(self) -> None:
        self._write_episode(
            "episode_bot_s1001",
            mode="bot",
            seed=1001,
            capture_hz=10,
            end_reason="goal",
        )
        selected = self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_episode(
            "episode_replay_s1000",
            mode="input-replay",
            seed=1000,
            capture_hz=0,
            end_reason="replay_source_end",
        )
        self._write_episode(
            "episode_human_s1000",
            mode="human",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_report()
        self._write_ml_dataset()

        evidence = publish_evidence(
            self.episodes_root,
            self.reports_root,
            self.output_root,
            create_video=False,
            generated_at=datetime(2026, 7, 31, 0, 0, tzinfo=UTC),
        )

        self.assertEqual(evidence["source_episode_id"], selected.name)
        self.assertEqual(evidence["git_revision"], "abc123def456")
        self.assertEqual(evidence["summary"]["valid_episode_count"], 4)
        self.assertTrue((self.output_root / "sample" / "manifest.json").is_file())
        self.assertTrue((self.output_root / "sample" / "rgb.png").is_file())
        self.assertTrue((self.output_root / "sample" / "depth.png").is_file())
        self.assertTrue((self.output_root / "sample" / "depth_preview.png").is_file())
        self.assertTrue((self.output_root / "runtime_first_person.png").is_file())
        self.assertTrue((self.output_root / "reports" / "summary.md").is_file())
        public_ml_manifest = json.loads(
            (self.output_root / "ml_dataset_manifest.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("source_episodes_root", public_ml_manifest)
        self.assertNotIn("files", public_ml_manifest)
        self.assertEqual(
            "simtrace_ml_dataset_summary",
            public_ml_manifest["artifact_type"],
        )
        self.assertTrue(public_ml_manifest["verified_complete"])
        self.assertFalse(public_ml_manifest["raw_episode_data_published"])
        self.assertFalse(public_ml_manifest["training_indexes_published"])
        excerpt = (self.output_root / "sample" / "trajectory_excerpt.jsonl").read_text(
            encoding="utf-8"
        )
        self.assertEqual(len(excerpt.strip().splitlines()), 3)
        copied_manifest = json.loads(
            (self.output_root / "sample" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(copied_manifest["episode_id"], selected.name)
        readme = (self.output_root / "README.md").read_text(encoding="utf-8")
        self.assertIn("1 human-play episode", readme)
        self.assertIn("Verified ML dataset summary", readme)
        self.assertIn(
            "The compact sample uses a deterministic bot episode.",
            readme,
        )
        self.assertNotIn("sample and evidence reel", readme)
        self.assertNotIn(
            "Human-play episodes are intentionally not represented",
            readme,
        )
        with Image.open(self.output_root / "sample" / "depth.png") as depth:
            self.assertIn(depth.mode, {"I;16", "I"})
        public_summary = json.loads(
            (self.output_root / "reports" / "summary.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("episodes_root", public_summary)
        self.assertNotIn("path", public_summary["episodes"][0])
        for relative_path, expected_hash in evidence["artifacts_sha256"].items():
            artifact = self.output_root / relative_path
            self.assertEqual(
                hashlib.sha256(artifact.read_bytes()).hexdigest(),
                expected_hash,
            )

    def test_prefers_latest_combat_episode_and_includes_fire_row(self) -> None:
        self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        combat_episode = self._write_episode(
            "episode_20260731T120000000Z_bot_s5150_00",
            mode="bot",
            seed=5150,
            capture_hz=10,
            end_reason="goal",
        )
        manifest_path = combat_episode / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            {
                "schema_version": 2,
                "shots_fired": 1,
                "shots_hit": 1,
                "shot_hit_rate": 1.0,
            }
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        trajectory_path = combat_episode / "trajectory.jsonl"
        rows = [
            json.loads(line)
            for line in trajectory_path.read_text(encoding="utf-8").splitlines()
        ]
        rows[1].update(
            {
                "fire_pressed": True,
                "combat_events": [
                    {"sequence": 0, "event": "fire", "shot_id": 0},
                    {"sequence": 1, "event": "shot", "shot_id": 0},
                    {
                        "sequence": 2,
                        "event": "hit",
                        "shot_id": 0,
                        "target_id": "target_alpha",
                        "distance_cm": 400.0,
                    },
                ],
            }
        )
        trajectory_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        self._write_report()

        evidence = publish_evidence(
            self.episodes_root,
            self.reports_root,
            self.output_root,
            create_video=False,
        )

        self.assertEqual(evidence["source_episode_id"], combat_episode.name)
        self.assertEqual(evidence["schema_version"], 2)
        self.assertEqual(evidence["source_shots_fired"], 1)
        self.assertEqual(evidence["source_shots_hit"], 1)
        self.assertEqual(evidence["source_combat_sim_frame"], 1)
        excerpt = [
            json.loads(line)
            for line in (self.output_root / "sample" / "trajectory_excerpt.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertTrue(any(row.get("fire_pressed") is True for row in excerpt))
        readme = (self.output_root / "README.md").read_text(encoding="utf-8")
        self.assertIn("Source combat ledger: 1 shot, 1 hit", readme)
        self.assertIn("Combat ledger plot", readme)

    def test_rejects_a_report_with_validation_errors(self) -> None:
        self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_report()
        summary_path = self.reports_root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["error_count"] = 1
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "validation errors"):
            publish_evidence(
                self.episodes_root,
                self.reports_root,
                self.output_root,
                create_video=False,
            )

    def test_rejects_report_for_a_different_episode_tree(self) -> None:
        self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_report()
        summary_path = self.reports_root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["episodes"][0]["episode_id"] = "episode_from_another_run"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "does not match episode tree"):
            publish_evidence(
                self.episodes_root,
                self.reports_root,
                self.output_root,
                create_video=False,
            )

    def test_rejects_stale_report_mode_counts(self) -> None:
        self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_episode(
            "episode_human_s1000",
            mode="human",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_report()
        summary_path = self.reports_root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["modes"] = {"bot": 2}
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        with self.assertRaisesRegex(
            ValueError,
            "report modes do not match episode tree",
        ):
            publish_evidence(
                self.episodes_root,
                self.reports_root,
                self.output_root,
                create_video=False,
            )

    def test_labels_report_without_human_episodes(self) -> None:
        self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_report()

        publish_evidence(
            self.episodes_root,
            self.reports_root,
            self.output_root,
            create_video=False,
        )

        readme = (self.output_root / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "No human-play episodes are included in this report.",
            readme,
        )
        self.assertNotIn("human-play episode, labeled by mode", readme)

    @patch("Scripts.publish_evidence._probe_duration", return_value=60.0)
    @patch("Scripts.publish_evidence._stream_video_frames")
    @patch("Scripts.publish_evidence.subprocess.Popen")
    @patch("Scripts.publish_evidence.shutil.which", return_value="ffmpeg")
    @patch("Scripts.publish_evidence._write_slide")
    def test_video_labels_source_revision_as_sample(
        self,
        write_slide,
        _which,
        _popen,
        _stream_frames,
        _probe_duration,
    ) -> None:
        episode = self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_report()
        summary = json.loads(
            (self.reports_root / "summary.json").read_text(encoding="utf-8")
        )
        manifest = json.loads((episode / "manifest.json").read_text(encoding="utf-8"))
        self.output_root.mkdir()

        _create_video(
            [(episode, manifest)],
            self.reports_root,
            self.output_root,
            summary,
            manifest["git_revision"],
        )

        title_lines = write_slide.call_args_list[0].args[2]
        self.assertIn("Sample revision: abc123def456", title_lines)
        self.assertNotIn("Measured revision: abc123def456", title_lines)

    def test_refresh_replaces_stale_output(self) -> None:
        self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_report()
        stale_replay = self.output_root / "sample" / "native_replay.replay"
        stale_replay.parent.mkdir(parents=True)
        stale_replay.write_bytes(b"stale replay")
        (self.output_root / "stale.txt").write_text("old run", encoding="utf-8")
        pubg_summary = self.output_root / "pubg" / "public-summary.json"
        pubg_summary.parent.mkdir()
        pubg_summary.write_text(
            json.dumps(self._public_pubg_summary()), encoding="utf-8"
        )

        evidence = publish_evidence(
            self.episodes_root,
            self.reports_root,
            self.output_root,
            create_video=False,
        )

        self.assertFalse(stale_replay.exists())
        self.assertFalse((self.output_root / "stale.txt").exists())
        self.assertTrue(pubg_summary.is_file())
        self.assertIn(
            "pubg/public-summary.json",
            evidence["artifacts_sha256"],
        )
        self.assertNotIn(
            "sample/native_replay.replay",
            evidence["artifacts_sha256"],
        )

    def test_refresh_rejects_unsanitized_pubg_evidence(self) -> None:
        self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_report()
        pubg_summary = self.output_root / "pubg" / "public-summary.json"
        pubg_summary.parent.mkdir(parents=True)
        unsafe = self._public_pubg_summary()
        unsafe["account_id"] = "must-not-publish"
        pubg_summary.write_text(json.dumps(unsafe), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "not sanitized"):
            publish_evidence(
                self.episodes_root,
                self.reports_root,
                self.output_root,
                create_video=False,
            )
        self.assertIn("must-not-publish", pubg_summary.read_text(encoding="utf-8"))

    def test_public_evidence_removes_host_fingerprint_values(self) -> None:
        episode = self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        manifest_path = episode / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["host_fingerprint"] = "private-host-sentinel"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        trajectory_path = episode / "trajectory.jsonl"
        rows = [
            json.loads(line)
            for line in trajectory_path.read_text(encoding="utf-8").splitlines()
        ]
        rows[0]["host_fingerprint"] = "private-host-sentinel"
        trajectory_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        self._write_report()
        summary_path = self.reports_root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["replay_comparisons"][0].update(
            {
                "original_host_fingerprint": "private-host-sentinel",
                "replayed_host_fingerprint": "private-host-sentinel",
                "host_fingerprint_match": True,
            }
        )
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        publish_evidence(
            self.episodes_root,
            self.reports_root,
            self.output_root,
            create_video=False,
        )

        public_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in self.output_root.rglob("*")
            if path.is_file() and path.suffix in {".json", ".jsonl", ".md"}
        )
        self.assertNotIn("private-host-sentinel", public_text)

    def test_rejects_contaminated_or_stale_ml_dataset(self) -> None:
        episode = self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_report()
        self._write_ml_dataset()
        dataset_path = self.root / "ml_dataset" / "dataset.json"
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
        dataset["operator_workspace"] = "C:/private/workspace"
        dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "fields do not match schema"):
            publish_evidence(
                self.episodes_root,
                self.reports_root,
                self.output_root,
                create_video=False,
            )

        dataset.pop("operator_workspace")
        dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
        manifest_path = episode / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["duration_s"] = 9.0
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(
            ValueError, "source_manifest_set_sha256 does not match"
        ):
            publish_evidence(
                self.episodes_root,
                self.reports_root,
                self.output_root,
                create_video=False,
            )

    def test_failed_refresh_preserves_previous_output(self) -> None:
        self._write_episode(
            "episode_bot_s1000",
            mode="bot",
            seed=1000,
            capture_hz=10,
            end_reason="goal",
        )
        self._write_report()
        self.output_root.mkdir()
        marker = self.output_root / "previous.txt"
        marker.write_text("keep me", encoding="utf-8")
        (self.reports_root / "capture_performance.png").unlink()

        with self.assertRaisesRegex(ValueError, "report artifact is missing"):
            publish_evidence(
                self.episodes_root,
                self.reports_root,
                self.output_root,
                create_video=False,
            )

        self.assertEqual(marker.read_text(encoding="utf-8"), "keep me")

    def test_ffmpeg_stream_failure_cleans_up_child(self) -> None:
        frame = self.root / "frame.png"
        Image.new("RGB", (320, 180), (10, 20, 30)).save(frame)

        class BrokenInput:
            def __init__(self) -> None:
                self.closed = False

            def write(self, _: bytes) -> None:
                raise BrokenPipeError("pipe closed")

            def close(self) -> None:
                self.closed = True

        class BrokenProcess:
            def __init__(self) -> None:
                self.stdin = BrokenInput()
                self.stderr = io.BytesIO(b"encoder stopped")
                self.killed = False
                self.waited = False

            def poll(self) -> None:
                return None

            def kill(self) -> None:
                self.killed = True

            def wait(self) -> int:
                self.waited = True
                return 1

        process = BrokenProcess()
        with self.assertRaisesRegex(RuntimeError, "encoder stopped"):
            _stream_video_frames(process, [frame])
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)


class PublishedEvidenceConsistencyTests(unittest.TestCase):
    def test_readme_and_public_sample_use_one_revision(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        evidence = json.loads(
            (repository_root / "docs" / "evidence" / "evidence.json").read_text(
                encoding="utf-8"
            )
        )
        sample = json.loads(
            (
                repository_root / "docs" / "evidence" / "sample" / "manifest.json"
            ).read_text(encoding="utf-8")
        )
        readme = (repository_root / "README.md").read_text(encoding="utf-8")

        self.assertEqual(evidence["git_revision"], sample["git_revision"])
        self.assertIn(sample["git_revision"], readme)


if __name__ == "__main__":
    unittest.main()
