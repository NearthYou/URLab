from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


API_BASE = "https://api.pubg.com/shards"
TELEMETRY_HOST = "telemetry-cdn.pubg.com"
PUBG_TELEMETRY_DOCS = "https://documentation.pubg.com/en/telemetry.html"
PUBG_EVENT_DOCS = (
    "https://documentation.pubg.com/en/telemetry-events.html"
)
PUBG_TERMS = "https://developer.pubg.com/tos?locale=en"
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9-]{1,128}$")
HTTP_TIMEOUT_SECONDS = 20.0
PUBLIC_SUMMARY_FIELDS = {
    "schema_version",
    "dataset",
    "match",
    "event_type_counts",
    "combat",
    "weapons",
    "position_samples",
    "phase_changes",
    "provenance",
}
PUBLIC_MATCH_FIELDS = {
    "platform",
    "match_id_sha256",
    "map_name",
    "team_size",
    "event_count",
    "first_event_utc",
    "last_event_utc",
}
PUBLIC_COMBAT_FIELDS = {
    "attacks",
    "damage_events",
    "knock_events",
    "kill_events",
    "attacks_with_damage",
    "attacks_with_knock",
    "attacks_with_kill",
    "unmatched_damage_attack_ids",
    "total_damage",
    "engagement_distance_raw",
}
PUBLIC_DISTRIBUTION_FIELDS = {"count", "mean", "p50", "p95"}
PUBLIC_PROVENANCE_FIELDS = {
    "source",
    "retrieved_utc",
    "telemetry_host",
    "source_url_sha256",
    "match_id_sha256",
    "raw_sha256",
    "raw_data_publishable",
    "contains_player_identifiers",
    "telemetry_docs",
    "event_docs",
    "terms",
}


class PubgTelemetryError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _validate_segment(value: str, label: str) -> str:
    if not SAFE_SEGMENT.fullmatch(value):
        raise PubgTelemetryError(
            f"{label} may contain only letters, numbers, and hyphens"
        )
    return value


def _require_saved_root(path: Path) -> Path:
    resolved = path.resolve()
    if "saved" not in {part.casefold() for part in resolved.parts}:
        raise PubgTelemetryError(
            "raw telemetry root must be inside a Saved directory"
        )
    return resolved


def _request_json(
    url: str,
    *,
    api_key: str | None = None,
    opener: Callable[..., Any] = urlopen,
) -> Any:
    headers = {
        "Accept": "application/vnd.api+json",
        "Accept-Encoding": "gzip",
        "User-Agent": "URLab-SimTrace/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(url, headers=headers)
    try:
        with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read()
            content_encoding = str(
                response.headers.get("Content-Encoding", "")
            ).casefold()
    except HTTPError as error:
        raise PubgTelemetryError(
            f"PUBG API request failed with HTTP {error.code}"
        ) from error
    except URLError as error:
        raise PubgTelemetryError("PUBG API request could not be completed") from error
    except (TimeoutError, OSError) as error:
        raise PubgTelemetryError(
            "PUBG API request could not be completed"
        ) from error

    if content_encoding == "gzip" or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError as error:
            raise PubgTelemetryError(
                "PUBG telemetry gzip payload is invalid"
            ) from error
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PubgTelemetryError("PUBG response is not valid UTF-8 JSON") from error


def extract_telemetry_url(match_payload: dict[str, Any]) -> str:
    try:
        asset_refs = match_payload["data"]["relationships"]["assets"]["data"]
        included = match_payload["included"]
    except (KeyError, TypeError) as error:
        raise PubgTelemetryError(
            "match response has no telemetry asset relationship"
        ) from error

    if not isinstance(asset_refs, list) or not isinstance(included, list):
        raise PubgTelemetryError(
            "match response has an invalid telemetry asset relationship"
        )
    asset_ids = {
        item.get("id")
        for item in asset_refs
        if isinstance(item, dict) and item.get("type") == "asset"
    }
    for item in included:
        if (
            not isinstance(item, dict)
            or item.get("type") != "asset"
            or item.get("id") not in asset_ids
        ):
            continue
        attributes = item.get("attributes")
        url = attributes.get("URL") if isinstance(attributes, dict) else None
        if not isinstance(url, str) or not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != TELEMETRY_HOST:
            raise PubgTelemetryError(
                "telemetry host is not the official PUBG CDN"
            )
        return url
    raise PubgTelemetryError("match response contains no telemetry URL")


def find_latest_match_id(
    *,
    platform: str,
    player_name: str,
    api_key: str,
    opener: Callable[..., Any] = urlopen,
) -> str:
    platform = _validate_segment(platform, "platform")
    if not player_name.strip():
        raise PubgTelemetryError("player name must not be empty")
    if not api_key.strip():
        raise PubgTelemetryError("PUBG_API_KEY is required")
    query = urlencode({"filter[playerNames]": player_name.strip()})
    payload = _request_json(
        f"{API_BASE}/{platform}/players?{query}",
        api_key=api_key,
        opener=opener,
    )
    try:
        matches = payload["data"][0]["relationships"]["matches"]["data"]
        match_id = matches[0]["id"]
    except (IndexError, KeyError, TypeError) as error:
        raise PubgTelemetryError(
            "player response contains no recent match"
        ) from error
    if not isinstance(match_id, str):
        raise PubgTelemetryError("player response contains an invalid match id")
    return _validate_segment(match_id, "match id")


def _finite_number(value: Any) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        return None
    return float(value)


def _distribution(values: list[float]) -> dict[str, int | float]:
    if not values:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0}
    ordered = sorted(values)

    def percentile(percent: float) -> float:
        index = (len(ordered) - 1) * percent
        lower = math.floor(index)
        upper = math.ceil(index)
        if lower == upper:
            return ordered[lower]
        fraction = index - lower
        return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction

    return {
        "count": len(ordered),
        "mean": sum(ordered) / len(ordered),
        "p50": percentile(0.5),
        "p95": percentile(0.95),
    }


def _kill_distance(event: dict[str, Any], event_type: str) -> float | None:
    if event_type == "LogPlayerKill":
        return _finite_number(event.get("distance"))
    for field in (
        "finishDamageInfo",
        "killerDamageInfo",
        "dBNODamageInfo",
    ):
        damage_info = event.get(field)
        if not isinstance(damage_info, dict):
            continue
        distance = _finite_number(damage_info.get("distance"))
        if distance is not None and distance >= 0:
            return distance
    return None


def aggregate_telemetry(
    events: list[dict[str, Any]],
    *,
    platform: str,
    match_id: str,
    source_url: str,
    retrieved_utc: str,
    raw_sha256: str,
) -> dict[str, Any]:
    platform = _validate_segment(platform, "platform")
    match_id = _validate_segment(match_id, "match id")
    if any(not isinstance(event, dict) for event in events):
        raise PubgTelemetryError("telemetry must be an array of event objects")

    event_types: Counter[str] = Counter()
    weapons: Counter[str] = Counter()
    attack_ids: set[int | str] = set()
    damage_attack_ids: set[int | str] = set()
    knock_attack_ids: set[int | str] = set()
    kill_attack_ids: set[int | str] = set()
    attack_count = 0
    damage_count = 0
    knock_count = 0
    kill_count = 0
    position_count = 0
    phase_changes = 0
    total_damage = 0.0
    engagement_distances: list[float] = []
    timestamps: list[str] = []
    map_name = ""
    team_size: int | None = None

    for event in events:
        event_type = event.get("_T")
        if not isinstance(event_type, str):
            event_type = "unknown"
        event_types[event_type] += 1
        timestamp = event.get("_D")
        if isinstance(timestamp, str):
            timestamps.append(timestamp)

        attack_id = event.get("attackId")
        valid_attack_id = isinstance(attack_id, (int, str)) and not isinstance(
            attack_id, bool
        )
        if event_type == "LogMatchStart":
            if isinstance(event.get("mapName"), str):
                map_name = event["mapName"]
            if isinstance(event.get("teamSize"), int) and not isinstance(
                event.get("teamSize"), bool
            ):
                team_size = event["teamSize"]
        elif event_type == "LogPlayerAttack":
            attack_count += 1
            if valid_attack_id:
                attack_ids.add(attack_id)
            weapon = event.get("weapon")
            weapon_id = (
                weapon.get("itemId") if isinstance(weapon, dict) else None
            )
            if isinstance(weapon_id, str) and weapon_id:
                weapons[weapon_id] += 1
        elif event_type == "LogPlayerTakeDamage":
            damage_count += 1
            if valid_attack_id:
                damage_attack_ids.add(attack_id)
            damage = _finite_number(event.get("damage"))
            if damage is not None and damage > 0:
                total_damage += damage
        elif event_type == "LogPlayerMakeGroggy":
            knock_count += 1
            if valid_attack_id:
                knock_attack_ids.add(attack_id)
            distance = _finite_number(event.get("distance"))
            if distance is not None and distance >= 0:
                engagement_distances.append(distance)
        elif event_type in {"LogPlayerKill", "LogPlayerKillV2"}:
            kill_count += 1
            if valid_attack_id:
                kill_attack_ids.add(attack_id)
            distance = _kill_distance(event, event_type)
            if distance is not None and distance >= 0:
                engagement_distances.append(distance)
        elif event_type == "LogPlayerPosition":
            position_count += 1
        elif event_type == "LogPhaseChange":
            phase_changes += 1

    match_hash = hashlib.sha256(match_id.encode("utf-8")).hexdigest()
    source_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "dataset": "pubg_telemetry_aggregate",
        "match": {
            "platform": platform,
            "match_id_sha256": match_hash,
            "map_name": map_name,
            "team_size": team_size,
            "event_count": len(events),
            "first_event_utc": min(timestamps) if timestamps else None,
            "last_event_utc": max(timestamps) if timestamps else None,
        },
        "event_type_counts": dict(sorted(event_types.items())),
        "combat": {
            "attacks": attack_count,
            "damage_events": damage_count,
            "knock_events": knock_count,
            "kill_events": kill_count,
            "attacks_with_damage": len(attack_ids & damage_attack_ids),
            "attacks_with_knock": len(attack_ids & knock_attack_ids),
            "attacks_with_kill": len(attack_ids & kill_attack_ids),
            "unmatched_damage_attack_ids": len(
                damage_attack_ids - attack_ids
            ),
            "total_damage": total_damage,
            "engagement_distance_raw": _distribution(
                engagement_distances
            ),
        },
        "weapons": dict(sorted(weapons.items())),
        "position_samples": position_count,
        "phase_changes": phase_changes,
        "provenance": {
            "source": "PUBG Developer API telemetry",
            "retrieved_utc": retrieved_utc,
            "telemetry_host": TELEMETRY_HOST,
            "source_url_sha256": source_hash,
            "match_id_sha256": match_hash,
            "raw_sha256": raw_sha256,
            "raw_data_publishable": False,
            "contains_player_identifiers": False,
            "telemetry_docs": PUBG_TELEMETRY_DOCS,
            "event_docs": PUBG_EVENT_DOCS,
            "terms": PUBG_TERMS,
        },
    }


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _write_json_atomic(path: Path, value: Any) -> None:
    payload = (
        json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    _write_bytes_atomic(path, payload)


def fetch_match_bundle(
    *,
    platform: str,
    match_id: str,
    api_key: str,
    simtrace_root: Path = Path("Saved/SimTrace"),
    opener: Callable[..., Any] = urlopen,
    retrieved_utc: str | None = None,
) -> dict[str, str]:
    platform = _validate_segment(platform, "platform")
    match_id = _validate_segment(match_id, "match id")
    if not api_key.strip():
        raise PubgTelemetryError("PUBG_API_KEY is required")
    simtrace_root = _require_saved_root(simtrace_root)
    retrieved_utc = retrieved_utc or _utc_now()

    match_url = f"{API_BASE}/{platform}/matches/{match_id}"
    match_payload = _request_json(
        match_url,
        api_key=api_key,
        opener=opener,
    )
    if not isinstance(match_payload, dict):
        raise PubgTelemetryError("match response must be a JSON object")
    telemetry_url = extract_telemetry_url(match_payload)
    telemetry_payload = _request_json(telemetry_url, opener=opener)
    if not isinstance(telemetry_payload, list) or any(
        not isinstance(event, dict) for event in telemetry_payload
    ):
        raise PubgTelemetryError(
            "telemetry response must be an array of event objects"
        )

    raw_directory = (
        simtrace_root / "imports" / "pubg" / "raw" / platform / match_id
    )
    derived_directory = (
        simtrace_root
        / "imports"
        / "pubg"
        / "derived"
        / platform
        / hashlib.sha256(match_id.encode("utf-8")).hexdigest()[:16]
    )
    match_path = raw_directory / "match.json"
    telemetry_path = raw_directory / "telemetry.json"
    telemetry_bytes = _json_bytes(telemetry_payload)
    raw_sha256 = hashlib.sha256(telemetry_bytes).hexdigest()
    _write_bytes_atomic(match_path, _json_bytes(match_payload))
    _write_bytes_atomic(telemetry_path, telemetry_bytes)
    _write_json_atomic(
        raw_directory / "provenance.private.json",
        {
            "schema_version": 1,
            "platform": platform,
            "match_id": match_id,
            "match_endpoint": match_url,
            "telemetry_url": telemetry_url,
            "retrieved_utc": retrieved_utc,
            "raw_sha256": raw_sha256,
            "publish_raw_data": False,
        },
    )

    summary = aggregate_telemetry(
        telemetry_payload,
        platform=platform,
        match_id=match_id,
        source_url=telemetry_url,
        retrieved_utc=retrieved_utc,
        raw_sha256=raw_sha256,
    )
    summary_path = derived_directory / "summary.json"
    _write_json_atomic(summary_path, summary)
    return {
        "raw_match_path": str(match_path),
        "raw_telemetry_path": str(telemetry_path),
        "private_provenance_path": str(
            raw_directory / "provenance.private.json"
        ),
        "summary_path": str(summary_path),
    }


def _summarize_local(
    telemetry_path: Path,
    *,
    platform: str,
    match_id: str,
    output: Path,
) -> dict[str, Any]:
    try:
        payload = telemetry_path.read_bytes()
        events = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PubgTelemetryError("local telemetry file is invalid") from error
    if not isinstance(events, list) or any(
        not isinstance(event, dict) for event in events
    ):
        raise PubgTelemetryError(
            "local telemetry must be an array of event objects"
        )
    summary = aggregate_telemetry(
        events,
        platform=platform,
        match_id=match_id,
        source_url=f"https://{TELEMETRY_HOST}/local-source",
        retrieved_utc=_utc_now(),
        raw_sha256=hashlib.sha256(payload).hexdigest(),
    )
    _write_json_atomic(output, summary)
    return summary


def _require_public_object(
    value: Any,
    fields: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PubgTelemetryError(f"public {label} must be an object")
    unexpected = sorted(value.keys() - fields)
    if unexpected:
        raise PubgTelemetryError(
            f"unexpected public field: {label}.{unexpected[0]}"
        )
    missing = sorted(fields - value.keys())
    if missing:
        raise PubgTelemetryError(
            f"missing public field: {label}.{missing[0]}"
        )
    return value


def _require_non_negative_int(value: Any, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise PubgTelemetryError(
            f"public {label} must be a non-negative integer"
        )
    return value


def _require_non_negative_number(value: Any, label: str) -> int | float:
    number = _finite_number(value)
    if number is None or number < 0:
        raise PubgTelemetryError(
            f"public {label} must be a non-negative finite number"
        )
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise PubgTelemetryError(f"public {label} must be a string")
    return value


def _require_optional_string(value: Any, label: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise PubgTelemetryError(
            f"public {label} must be a string or null"
        )
    return value


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise PubgTelemetryError(f"public {label} must be a SHA-256 hash")
    return value


def _copy_count_map(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise PubgTelemetryError(f"public {label} must be an object")
    copied: dict[str, int] = {}
    for key, count in value.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key)
        ):
            raise PubgTelemetryError(
                f"public {label} contains an invalid key"
            )
        copied[key] = _require_non_negative_int(
            count,
            f"{label}.{key}",
        )
    return dict(sorted(copied.items()))


def _copy_distribution(value: Any) -> dict[str, int | float]:
    distribution = _require_public_object(
        value,
        PUBLIC_DISTRIBUTION_FIELDS,
        "combat.engagement_distance_raw",
    )
    return {
        "count": _require_non_negative_int(
            distribution["count"],
            "combat.engagement_distance_raw.count",
        ),
        "mean": _require_non_negative_number(
            distribution["mean"],
            "combat.engagement_distance_raw.mean",
        ),
        "p50": _require_non_negative_number(
            distribution["p50"],
            "combat.engagement_distance_raw.p50",
        ),
        "p95": _require_non_negative_number(
            distribution["p95"],
            "combat.engagement_distance_raw.p95",
        ),
    }


def _build_public_summary(summary: dict[str, Any]) -> dict[str, Any]:
    top_level = _require_public_object(
        summary,
        PUBLIC_SUMMARY_FIELDS,
        "summary",
    )
    if top_level.get("schema_version") != 1:
        raise PubgTelemetryError("public schema_version must be 1")
    if top_level.get("dataset") != "pubg_telemetry_aggregate":
        raise PubgTelemetryError("summary was not produced by this importer")

    match = _require_public_object(
        top_level["match"],
        PUBLIC_MATCH_FIELDS,
        "match",
    )
    combat = _require_public_object(
        top_level["combat"],
        PUBLIC_COMBAT_FIELDS,
        "combat",
    )
    provenance = _require_public_object(
        top_level["provenance"],
        PUBLIC_PROVENANCE_FIELDS,
        "provenance",
    )
    match_hash = _require_sha256(
        match["match_id_sha256"],
        "match.match_id_sha256",
    )
    if (
        provenance.get("raw_data_publishable") is not False
        or provenance.get("contains_player_identifiers") is not False
    ):
        raise PubgTelemetryError(
            "summary is not marked as public-safe aggregate data"
        )
    if provenance.get("telemetry_host") != TELEMETRY_HOST:
        raise PubgTelemetryError("summary telemetry host is invalid")
    if _require_sha256(
        provenance["match_id_sha256"],
        "provenance.match_id_sha256",
    ) != match_hash:
        raise PubgTelemetryError("summary match hashes do not agree")

    combat_counts = {
        field: _require_non_negative_int(combat[field], f"combat.{field}")
        for field in (
            "attacks",
            "damage_events",
            "knock_events",
            "kill_events",
            "attacks_with_damage",
            "attacks_with_knock",
            "attacks_with_kill",
            "unmatched_damage_attack_ids",
        )
    }
    return {
        "schema_version": 1,
        "dataset": "pubg_telemetry_aggregate",
        "match": {
            "platform": _validate_segment(
                _require_string(match["platform"], "match.platform"),
                "summary platform",
            ),
            "match_id_sha256": match_hash,
            "map_name": _require_string(
                match["map_name"],
                "match.map_name",
            ),
            "team_size": (
                None
                if match["team_size"] is None
                else _require_non_negative_int(
                    match["team_size"],
                    "match.team_size",
                )
            ),
            "event_count": _require_non_negative_int(
                match["event_count"],
                "match.event_count",
            ),
            "first_event_utc": _require_optional_string(
                match["first_event_utc"],
                "match.first_event_utc",
            ),
            "last_event_utc": _require_optional_string(
                match["last_event_utc"],
                "match.last_event_utc",
            ),
        },
        "event_type_counts": _copy_count_map(
            top_level["event_type_counts"],
            "event_type_counts",
        ),
        "combat": {
            **combat_counts,
            "total_damage": _require_non_negative_number(
                combat["total_damage"],
                "combat.total_damage",
            ),
            "engagement_distance_raw": _copy_distribution(
                combat["engagement_distance_raw"]
            ),
        },
        "weapons": _copy_count_map(top_level["weapons"], "weapons"),
        "position_samples": _require_non_negative_int(
            top_level["position_samples"],
            "position_samples",
        ),
        "phase_changes": _require_non_negative_int(
            top_level["phase_changes"],
            "phase_changes",
        ),
        "provenance": {
            "source": _require_string(
                provenance["source"],
                "provenance.source",
            ),
            "retrieved_utc": _require_string(
                provenance["retrieved_utc"],
                "provenance.retrieved_utc",
            ),
            "telemetry_host": TELEMETRY_HOST,
            "source_url_sha256": _require_sha256(
                provenance["source_url_sha256"],
                "provenance.source_url_sha256",
            ),
            "match_id_sha256": match_hash,
            "raw_sha256": _require_sha256(
                provenance["raw_sha256"],
                "provenance.raw_sha256",
            ),
            "raw_data_publishable": False,
            "contains_player_identifiers": False,
            "telemetry_docs": _require_string(
                provenance["telemetry_docs"],
                "provenance.telemetry_docs",
            ),
            "event_docs": _require_string(
                provenance["event_docs"],
                "provenance.event_docs",
            ),
            "terms": _require_string(
                provenance["terms"],
                "provenance.terms",
            ),
        },
    }


def publish_summary(source: Path, output: Path) -> dict[str, Any]:
    try:
        summary = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PubgTelemetryError("aggregate summary is invalid") from error
    if not isinstance(summary, dict):
        raise PubgTelemetryError("aggregate summary must be a JSON object")
    public_summary = _build_public_summary(summary)
    _write_json_atomic(output, public_summary)
    return public_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch PUBG telemetry into Saved/ and emit a public-safe "
            "aggregate without player identifiers"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.add_argument("--platform", default="steam")
    source = fetch_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--match-id")
    source.add_argument("--player-name")
    fetch_parser.add_argument(
        "--simtrace-root",
        type=Path,
        default=Path("Saved/SimTrace"),
    )

    summarize_parser = subparsers.add_parser("summarize")
    summarize_parser.add_argument("telemetry", type=Path)
    summarize_parser.add_argument("--platform", default="steam")
    summarize_parser.add_argument("--match-id", required=True)
    summarize_parser.add_argument("--output", type=Path, required=True)

    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("summary", type=Path)
    publish_parser.add_argument("--output", type=Path, required=True)

    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "fetch":
            api_key = os.environ.get("PUBG_API_KEY", "")
            match_id = arguments.match_id
            if arguments.player_name:
                match_id = find_latest_match_id(
                    platform=arguments.platform,
                    player_name=arguments.player_name,
                    api_key=api_key,
                )
            result = fetch_match_bundle(
                platform=arguments.platform,
                match_id=match_id,
                api_key=api_key,
                simtrace_root=arguments.simtrace_root,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if arguments.command == "summarize":
            summary = _summarize_local(
                arguments.telemetry,
                platform=arguments.platform,
                match_id=arguments.match_id,
                output=arguments.output,
            )
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            return 0
        if arguments.command == "publish":
            summary = publish_summary(arguments.summary, arguments.output)
            print(
                json.dumps(
                    {
                        "output": str(arguments.output.resolve()),
                        "event_count": summary["match"]["event_count"],
                        "shots": summary["combat"]["attacks"],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
    except PubgTelemetryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
