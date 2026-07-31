from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from Scripts.pubg_telemetry import (
    PubgTelemetryError,
    aggregate_telemetry,
    extract_telemetry_url,
    fetch_match_bundle,
    publish_summary,
)


FIXTURE = (
    Path(__file__).parent / "fixtures" / "pubg_telemetry_synthetic.json"
)


class FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        content_encoding: str | None = None,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.body = gzip.compress(body) if content_encoding == "gzip" else body
        self.headers = (
            {"Content-Encoding": content_encoding} if content_encoding else {}
        )

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class TimeoutResponse(FakeResponse):
    def __init__(self) -> None:
        super().__init__({})

    def read(self) -> bytes:
        raise TimeoutError("synthetic read timeout")


class PubgTelemetryTests(unittest.TestCase):
    def test_aggregates_attack_outcomes_without_player_identifiers(self) -> None:
        events = json.loads(FIXTURE.read_text(encoding="utf-8"))

        summary = aggregate_telemetry(
            events,
            platform="steam",
            match_id="synthetic-match",
            source_url="https://telemetry-cdn.pubg.com/test/telemetry.json",
            retrieved_utc="2026-07-31T00:10:00Z",
            raw_sha256="a" * 64,
        )

        self.assertEqual(2, summary["combat"]["attacks"])
        self.assertEqual(1, summary["combat"]["attacks_with_damage"])
        self.assertEqual(1, summary["combat"]["attacks_with_knock"])
        self.assertEqual(1, summary["combat"]["attacks_with_kill"])
        self.assertEqual(28.5, summary["combat"]["total_damage"])
        self.assertEqual(
            2,
            summary["combat"]["engagement_distance_raw"]["count"],
        )
        self.assertEqual(
            1525.0,
            summary["combat"]["engagement_distance_raw"]["p50"],
        )
        self.assertEqual({"Item_Weapon_M416_C": 2}, summary["weapons"])
        self.assertNotIn("synthetic-account-a", json.dumps(summary))
        self.assertNotIn("SyntheticAlpha", json.dumps(summary))
        self.assertFalse(summary["provenance"]["raw_data_publishable"])
        self.assertEqual(64, len(summary["provenance"]["match_id_sha256"]))

    def test_extracts_the_related_telemetry_asset(self) -> None:
        match = {
            "data": {
                "relationships": {
                    "assets": {
                        "data": [{"type": "asset", "id": "asset-1"}]
                    }
                }
            },
            "included": [
                {
                    "type": "asset",
                    "id": "asset-1",
                    "attributes": {
                        "URL": (
                            "https://telemetry-cdn.pubg.com/"
                            "test/telemetry.json"
                        )
                    },
                }
            ],
        }

        self.assertEqual(
            "https://telemetry-cdn.pubg.com/test/telemetry.json",
            extract_telemetry_url(match),
        )

    def test_rejects_a_non_pubg_telemetry_host(self) -> None:
        match = {
            "data": {
                "relationships": {
                    "assets": {
                        "data": [{"type": "asset", "id": "asset-1"}]
                    }
                }
            },
            "included": [
                {
                    "type": "asset",
                    "id": "asset-1",
                    "attributes": {
                        "URL": "https://example.com/telemetry.json"
                    },
                }
            ],
        }

        with self.assertRaisesRegex(PubgTelemetryError, "telemetry host"):
            extract_telemetry_url(match)

    def test_fetch_uses_bearer_key_and_keeps_raw_data_under_saved(self) -> None:
        telemetry_url = (
            "https://telemetry-cdn.pubg.com/test/telemetry.json"
        )
        match = {
            "data": {
                "relationships": {
                    "assets": {
                        "data": [{"type": "asset", "id": "asset-1"}]
                    }
                }
            },
            "included": [
                {
                    "type": "asset",
                    "id": "asset-1",
                    "attributes": {"URL": telemetry_url},
                }
            ],
        }
        events = json.loads(FIXTURE.read_text(encoding="utf-8"))
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            if request.full_url == telemetry_url:
                return FakeResponse(events, content_encoding="gzip")
            return FakeResponse(match)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Saved" / "SimTrace"
            result = fetch_match_bundle(
                platform="steam",
                match_id="synthetic-match",
                api_key="secret-test-key",
                simtrace_root=root,
                opener=opener,
                retrieved_utc="2026-07-31T00:10:00Z",
            )

            self.assertTrue(Path(result["raw_telemetry_path"]).is_file())
            self.assertTrue(Path(result["summary_path"]).is_file())
            self.assertTrue(
                Path(result["raw_telemetry_path"]).is_relative_to(root)
            )
            self.assertEqual(
                "Bearer secret-test-key",
                requests[0][0].get_header("Authorization"),
            )
            self.assertIsNone(requests[1][0].get_header("Authorization"))
            published = json.loads(
                Path(result["summary_path"]).read_text(encoding="utf-8")
            )
            self.assertNotIn("secret-test-key", json.dumps(published))

    def test_fetch_rejects_an_empty_api_key(self) -> None:
        with self.assertRaisesRegex(PubgTelemetryError, "PUBG_API_KEY"):
            fetch_match_bundle(
                platform="steam",
                match_id="synthetic-match",
                api_key="",
                simtrace_root=Path("Saved/SimTrace"),
            )

    def test_fetch_translates_response_read_timeout(self) -> None:
        with self.assertRaisesRegex(PubgTelemetryError, "could not be completed"):
            fetch_match_bundle(
                platform="steam",
                match_id="synthetic-match",
                api_key="secret-test-key",
                simtrace_root=Path("Saved/SimTrace"),
                opener=lambda request, timeout: TimeoutResponse(),
            )

    def test_publish_copies_only_a_generated_anonymous_summary(self) -> None:
        events = json.loads(FIXTURE.read_text(encoding="utf-8"))
        summary = aggregate_telemetry(
            events,
            platform="steam",
            match_id="synthetic-match",
            source_url="https://telemetry-cdn.pubg.com/test/telemetry.json",
            retrieved_utc="2026-07-31T00:10:00Z",
            raw_sha256="a" * 64,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Saved" / "SimTrace" / "summary.json"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps(summary), encoding="utf-8")
            output = root / "docs" / "evidence" / "pubg_summary.json"

            publish_summary(source, output)

            published = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("pubg_telemetry_aggregate", published["dataset"])
            self.assertFalse(published["provenance"]["raw_data_publishable"])

            summary["account_id"] = "must-not-publish"
            source.write_text(json.dumps(summary), encoding="utf-8")
            with self.assertRaisesRegex(
                PubgTelemetryError, "unexpected public field"
            ):
                publish_summary(source, output)

            for container, field, value in (
                ("match", "account_id", "must-not-publish"),
                ("combat", "raw_events", [{"accountId": "secret"}]),
                ("provenance", "player_name", "must-not-publish"),
            ):
                contaminated = aggregate_telemetry(
                    events,
                    platform="steam",
                    match_id="synthetic-match",
                    source_url=(
                        "https://telemetry-cdn.pubg.com/"
                        "test/telemetry.json"
                    ),
                    retrieved_utc="2026-07-31T00:10:00Z",
                    raw_sha256="a" * 64,
                )
                contaminated[container][field] = value
                source.write_text(
                    json.dumps(contaminated),
                    encoding="utf-8",
                )
                with self.subTest(container=container, field=field):
                    with self.assertRaisesRegex(
                        PubgTelemetryError,
                        "unexpected public field",
                    ):
                        publish_summary(source, output)


if __name__ == "__main__":
    unittest.main()
