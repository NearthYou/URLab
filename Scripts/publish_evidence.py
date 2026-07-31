from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from itertools import cycle, islice
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


PLOT_NAMES = (
    "episode_sizes.png",
    "episode_outcomes.png",
    "action_distributions.png",
    "combat_ledger.png",
    "replay_error.png",
    "capture_performance.png",
)
VIDEO_NAME = "simtrace_demo.mp4"
VIDEO_WIDTH = 640
VIDEO_HEIGHT = 360
REPORT_IDENTITY_FIELDS = (
    "mode",
    "seed",
    "course_hash",
    "end_reason",
    "complete",
    "trajectory_frames",
    "capture_frames",
    "capture_dropped",
)


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
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain an object")
            rows.append(value)
    if not rows:
        raise ValueError(f"{path} is empty")
    return rows


def _episode_manifests(episodes_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not episodes_root.is_dir():
        raise ValueError(f"episodes root is missing: {episodes_root}")
    episodes: list[tuple[Path, dict[str, Any]]] = []
    for episode in sorted(path for path in episodes_root.iterdir() if path.is_dir()):
        manifest_path = episode / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError(f"episode has no completed manifest: {episode}")
        manifest = _load_json(manifest_path)
        if manifest.get("episode_id") != episode.name:
            raise ValueError(f"manifest episode_id does not match directory: {episode}")
        episodes.append((episode, manifest))
    if not episodes:
        raise ValueError("no completed episodes were found")
    return episodes


def _verify_report_matches_episodes(
    summary: dict[str, Any],
    episodes: list[tuple[Path, dict[str, Any]]],
) -> None:
    reported_rows = summary.get("episodes")
    if not isinstance(reported_rows, list):
        raise ValueError("report does not contain episode details")
    reported: dict[str, dict[str, Any]] = {}
    for row in reported_rows:
        if not isinstance(row, dict) or not isinstance(row.get("episode_id"), str):
            raise ValueError("report contains an invalid episode entry")
        episode_id = row["episode_id"]
        if episode_id in reported:
            raise ValueError(f"report contains duplicate episode_id: {episode_id}")
        reported[episode_id] = row

    actual = {manifest["episode_id"]: manifest for _, manifest in episodes}
    if set(reported) != set(actual):
        missing = sorted(set(actual) - set(reported))
        extra = sorted(set(reported) - set(actual))
        raise ValueError(
            "report does not match episode tree "
            f"(missing={missing}, extra={extra})"
        )
    if summary.get("episode_count") != len(actual):
        raise ValueError("report episode_count does not match episode tree")
    if summary.get("valid_episode_count") != len(actual):
        raise ValueError("not every episode in the report is valid")

    actual_modes: dict[str, int] = {}
    for episode_id, manifest in actual.items():
        mode = manifest.get("mode")
        if not isinstance(mode, str):
            raise ValueError(f"manifest has invalid mode: {episode_id}")
        actual_modes[mode] = actual_modes.get(mode, 0) + 1
        report_row = reported[episode_id]
        for field in REPORT_IDENTITY_FIELDS:
            if report_row.get(field) != manifest.get(field):
                raise ValueError(
                    f"report does not match {episode_id} field {field}"
                )
    if summary.get("modes") != actual_modes:
        raise ValueError("report modes do not match episode tree")


def _captured_goal_episodes(
    episodes: list[tuple[Path, dict[str, Any]]],
) -> list[tuple[Path, dict[str, Any]]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for episode, manifest in episodes:
        if (
            manifest.get("mode") == "bot"
            and manifest.get("capture_hz", 0) > 0
            and manifest.get("capture_frames", 0) > 0
            and manifest.get("capture_dropped") == 0
            and manifest.get("end_reason") == "goal"
            and manifest.get("complete") is True
        ):
            candidates.append((episode, manifest))
    candidates.sort(
        key=lambda item: (
            int(item[1].get("seed", 0)),
            str(item[1].get("episode_id", item[0].name)),
        )
    )
    if not candidates:
        raise ValueError("no complete captured bot goal episode was found")
    return candidates


def _representative_episode(
    candidates: list[tuple[Path, dict[str, Any]]],
) -> tuple[Path, dict[str, Any]]:
    combat_candidates = [
        candidate
        for candidate in candidates
        if int(candidate[1].get("schema_version", 0)) >= 2
        and int(candidate[1].get("shots_fired", 0)) > 0
    ]
    if combat_candidates:
        return max(
            combat_candidates,
            key=lambda item: str(item[1].get("episode_id", item[0].name)),
        )
    return candidates[0]


def _public_summary(summary: dict[str, Any]) -> dict[str, Any]:
    public = dict(summary)
    public.pop("episodes_root", None)
    public["episodes"] = [
        {key: value for key, value in episode.items() if key != "path"}
        for episode in summary.get("episodes", [])
        if isinstance(episode, dict)
    ]
    return public


def _trajectory_excerpt(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexes = sorted({0, len(rows) // 2, len(rows) - 1})
    combat_indexes = [
        index
        for index, row in enumerate(rows)
        if isinstance(row.get("combat_events"), list)
        and len(row["combat_events"]) > 0
    ]
    if combat_indexes:
        indexes = sorted(set(indexes + [combat_indexes[0], combat_indexes[-1]]))
    return [rows[index] for index in indexes]


def _depth_preview(source: Path, destination: Path) -> None:
    with Image.open(source) as image:
        depth = np.asarray(image, dtype=np.float64)
    valid = depth > 0
    preview = np.zeros(depth.shape, dtype=np.uint8)
    if np.any(valid):
        lower, upper = np.percentile(depth[valid], [2.0, 98.0])
        if upper <= lower:
            upper = lower + 1.0
        normalized = np.clip((depth - lower) / (upper - lower), 0.0, 1.0)
        preview[valid] = np.round(normalized[valid] * 255.0).astype(np.uint8)
    Image.fromarray(preview).save(destination)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ):
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _fit_image(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    result = ImageOps.contain(
        image.convert("RGB"), size, Image.Resampling.LANCZOS
    )
    canvas = Image.new("RGB", size, (14, 19, 28))
    x = (size[0] - result.width) // 2
    y = (size[1] - result.height) // 2
    canvas.paste(result, (x, y))
    return canvas


def _write_rgb_depth_panel(
    rgb_path: Path, depth_preview_path: Path, destination: Path
) -> None:
    canvas = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (14, 19, 28))
    label_font = _font(22)
    with Image.open(rgb_path) as rgb, Image.open(depth_preview_path) as depth:
        canvas.paste(_fit_image(rgb, (300, 260)), (10, 55))
        canvas.paste(_fit_image(depth, (300, 260)), (330, 55))
    draw = ImageDraw.Draw(canvas)
    draw.text((112, 18), "RGB", font=label_font, fill=(238, 242, 247))
    draw.text((438, 18), "16-bit depth preview", font=label_font, fill=(238, 242, 247))
    draw.text(
        (155, 325),
        "Same simulation frame",
        font=_font(18),
        fill=(80, 210, 170),
    )
    canvas.save(destination)


def _write_slide(
    destination: Path,
    title: str,
    lines: list[str],
    *,
    accent: tuple[int, int, int] = (80, 210, 170),
) -> None:
    image = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (14, 19, 28))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 12, VIDEO_HEIGHT), fill=accent)
    draw.text((42, 35), title, font=_font(36), fill=(245, 247, 250))
    y = 115
    for line in lines:
        draw.text((44, y), line, font=_font(22), fill=(200, 211, 224))
        y += 43
    image.save(destination)


def _probe_duration(video: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def _stream_video_frames(
    process: subprocess.Popen[bytes],
    frame_paths: list[Path],
) -> None:
    assert process.stdin is not None
    assert process.stderr is not None
    stream_error: BaseException | None = None
    try:
        for frame_path in frame_paths:
            with Image.open(frame_path) as image:
                frame = _fit_image(image, (VIDEO_WIDTH, VIDEO_HEIGHT))
            process.stdin.write(frame.tobytes())
    except BaseException as error:
        stream_error = error
    try:
        process.stdin.close()
    except BrokenPipeError as error:
        stream_error = stream_error or error

    if stream_error is not None and process.poll() is None:
        process.kill()
    stderr = process.stderr.read().decode("utf-8", errors="replace")
    process.stderr.close()
    return_code = process.wait()

    if stream_error is not None:
        if isinstance(stream_error, (KeyboardInterrupt, SystemExit)):
            raise stream_error
        raise RuntimeError(
            f"ffmpeg frame stream failed:\n{stderr[-4000:]}"
        ) from stream_error
    if return_code != 0:
        raise RuntimeError(f"ffmpeg failed:\n{stderr[-4000:]}")


def _create_video(
    captured_episodes: list[tuple[Path, dict[str, Any]]],
    reports_root: Path,
    output_root: Path,
    summary: dict[str, Any],
    sample_git_revision: str,
) -> float | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("ffmpeg is required to create the 60-second demo")

    gameplay_frames: list[Path] = []
    for episode, _ in captured_episodes:
        gameplay_frames.extend(sorted((episode / "rgb").glob("*.png")))
    if not gameplay_frames:
        raise ValueError("no RGB frames were found for the demo video")

    with tempfile.TemporaryDirectory(prefix="simtrace-evidence-") as temporary:
        temporary_root = Path(temporary)
        title_slide = temporary_root / "title.png"
        summary_slide = temporary_root / "summary.png"
        _write_slide(
            title_slide,
            "Unreal SimTrace",
            [
                "Runtime-generated AI data collection",
                f"Sample revision: {sample_git_revision[:12]}",
                "30 Hz state and action, 10 Hz RGB and depth",
            ],
        )
        comparisons = summary.get("replay_comparisons", [])
        max_mean_error = max(
            (
                float(item.get("mean_position_error_cm", 0.0))
                for item in comparisons
                if isinstance(item, dict)
            ),
            default=0.0,
        )
        _write_slide(
            summary_slide,
            "Measured results",
            [
                f"Valid episodes: {summary.get('valid_episode_count', 0)}",
                f"Capture drops: {summary.get('capture_dropped', 0)}",
                f"Replay comparisons: {len(comparisons)}",
                f"Max mean path error: {max_mean_error:.6f} cm",
            ],
        )

        pair_path = output_root / "rgb_depth_pair.png"
        replay_plot = reports_root / "replay_error.png"
        if not replay_plot.is_file():
            raise ValueError(f"report artifact is missing: {replay_plot}")

        frame_paths = (
            [title_slide] * 50
            + list(islice(cycle(gameplay_frames), 400))
            + [pair_path] * 50
            + [replay_plot] * 50
            + [summary_slide] * 50
        )
        if len(frame_paths) != 600:
            raise AssertionError("demo timeline must contain exactly 600 frames")
        destination = output_root / VIDEO_NAME
        process = subprocess.Popen(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "rawvideo",
                "-pixel_format",
                "rgb24",
                "-video_size",
                f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}",
                "-framerate",
                "10",
                "-i",
                "pipe:0",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "24",
                "-movflags",
                "+faststart",
                str(destination),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _stream_video_frames(process, frame_paths)
    duration = _probe_duration(output_root / VIDEO_NAME)
    if duration is None or not 59.9 <= duration <= 60.1:
        raise RuntimeError(f"demo video duration is not 60 seconds: {duration}")
    return duration


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_evidence_readme(
    output_root: Path,
    evidence: dict[str, Any],
    *,
    has_video: bool,
    has_ml_manifest: bool,
) -> None:
    summary = evidence["summary"]
    modes = summary.get("modes", {})
    human_episode_count = (
        int(modes.get("human", 0)) if isinstance(modes, dict) else 0
    )
    lines = [
        "# Recorded evidence",
        "",
        "This directory contains a compact, tracked extract of a real SimTrace run.",
        "The full generated dataset remains under `Saved/SimTrace` and is ignored by Git.",
        "",
        f"- Source episode: `{evidence['source_episode_id']}`",
        f"- Git revision recorded by Unreal: `{evidence['git_revision']}`",
        f"- Valid episodes: {summary['valid_episode_count']}",
        f"- Missing captures: {summary['missing_capture_frames']}",
        f"- Capture drops: {summary['capture_dropped']}",
        f"- Replay comparisons: {summary['replay_comparison_count']}",
        (
            "- Source combat ledger: "
            f"{evidence['source_shots_fired']} shot, "
            f"{evidence['source_shots_hit']} hit"
        ),
        "",
        "## Runtime view",
        "",
        "![First-person RGB captured during the run](runtime_first_person.png)",
        "",
        "## Synchronized RGB and depth",
        "",
        "![RGB and depth from the same simulation frame](rgb_depth_pair.png)",
        "",
        "Raw files are in [`sample`](sample), including the original 16-bit depth PNG,",
        "the episode manifest, a trajectory excerpt, and the native Replay archive when",
        "one was present.",
        "",
        "## Reports",
        "",
        "- [Validation summary](reports/summary.md)",
        "- [Machine-readable summary](reports/summary.json)",
        "- [Combat ledger plot](reports/combat_ledger.png)",
        "- [Replay error plot](reports/replay_error.png)",
        "- [Capture performance plot](reports/capture_performance.png)",
    ]
    if has_ml_manifest:
        lines.extend(
            [
                "",
                "## ML consumption contract",
                "",
                "- [ML dataset manifest](ml_dataset_manifest.json)",
                "",
                "Only the validated counts, feature schema, causal alignment, "
                "and seed-level split contract are published. Raw episodes and "
                "generated training indexes remain under `Saved/SimTrace`.",
            ]
        )
    if has_video:
        lines.extend(
            [
                "",
                "## 60-second evidence reel",
                "",
                f"[Open the MP4]({VIDEO_NAME})",
                "",
                "The reel is generated only from recorded RGB frames and report artifacts.",
            ]
        )
    lines.append("")
    if has_video:
        lines.append(
            "The compact sample and evidence reel use deterministic bot episodes."
        )
    else:
        lines.append("The compact sample uses a deterministic bot episode.")
    if human_episode_count:
        noun = "episode" if human_episode_count == 1 else "episodes"
        lines.append(
            "The validation report also includes "
            f"{human_episode_count} human-play {noun}, labeled by mode."
        )
    else:
        lines.append("No human-play episodes are included in this report.")
    lines.append("")
    (output_root / "README.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def _publish_ml_dataset_manifest(
    episodes_root: Path, output_root: Path
) -> dict[str, Any] | None:
    source_path = episodes_root.parent / "ml_dataset" / "dataset.json"
    if not source_path.is_file():
        return None
    source = _load_json(source_path)
    if source.get("complete") is not True:
        raise ValueError(f"ML dataset manifest is incomplete: {source_path}")
    for field in (
        "episode_count",
        "transition_count",
        "sensor_policy_sample_count",
    ):
        value = source.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"ML dataset manifest has invalid {field}")

    public = {
        key: value
        for key, value in source.items()
        if key not in {"source_episodes_root", "files"}
    }
    public.update(
        {
            "raw_episode_data_published": False,
            "training_indexes_published": False,
            "source_manifest_sha256": _sha256(source_path),
        }
    )
    (output_root / "ml_dataset_manifest.json").write_text(
        json.dumps(public, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return public


def _build_evidence_bundle(
    episodes_root: Path,
    reports_root: Path,
    output_root: Path,
    *,
    create_video: bool = True,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    episodes_root = episodes_root.resolve()
    reports_root = reports_root.resolve()
    output_root = output_root.resolve()
    summary_path = reports_root / "summary.json"
    if not summary_path.is_file():
        raise ValueError(f"report summary is missing: {summary_path}")
    summary = _load_json(summary_path)
    if summary.get("error_count") != 0:
        raise ValueError("report contains validation errors")
    if summary.get("warning_count") != 0:
        raise ValueError("report contains validation warnings")
    if summary.get("valid_episode_count") != summary.get("episode_count"):
        raise ValueError("not every episode in the report is valid")

    episodes = _episode_manifests(episodes_root)
    _verify_report_matches_episodes(summary, episodes)
    captured_episodes = _captured_goal_episodes(episodes)
    source_episode, manifest = _representative_episode(captured_episodes)
    rows = _load_trajectory(source_episode / "trajectory.jsonl")
    captured_rows = [
        row
        for row in rows
        if row.get("captured") is True
        and isinstance(row.get("rgb_path"), str)
        and isinstance(row.get("depth_path"), str)
    ]
    if not captured_rows:
        raise ValueError("representative episode has no captured trajectory row")
    combat_rows = [
        row
        for row in rows
        if isinstance(row.get("combat_events"), list)
        and len(row["combat_events"]) > 0
    ]
    if combat_rows:
        combat_frame = int(combat_rows[0]["sim_frame"])
        sample_row = min(
            captured_rows,
            key=lambda row: (
                abs(int(row["sim_frame"]) - combat_frame),
                int(row["sim_frame"]),
            ),
        )
    else:
        combat_frame = None
        sample_row = captured_rows[len(captured_rows) // 2]
    rgb_source = source_episode / str(sample_row["rgb_path"])
    depth_source = source_episode / str(sample_row["depth_path"])
    if not rgb_source.is_file() or not depth_source.is_file():
        raise ValueError("representative RGB/depth pair is missing")

    sample_output = output_root / "sample"
    reports_output = output_root / "reports"
    sample_output.mkdir(parents=True, exist_ok=True)
    reports_output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_episode / "manifest.json", sample_output / "manifest.json")
    shutil.copy2(rgb_source, sample_output / "rgb.png")
    shutil.copy2(depth_source, sample_output / "depth.png")
    _depth_preview(depth_source, sample_output / "depth_preview.png")
    shutil.copy2(rgb_source, output_root / "runtime_first_person.png")
    _write_rgb_depth_panel(
        sample_output / "rgb.png",
        sample_output / "depth_preview.png",
        output_root / "rgb_depth_pair.png",
    )
    excerpt = _trajectory_excerpt(rows)
    (sample_output / "trajectory_excerpt.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in excerpt
        ),
        encoding="utf-8",
    )
    replay_relative = manifest.get("replay_archive_path")
    if isinstance(replay_relative, str) and replay_relative:
        replay_source = source_episode / replay_relative
        if replay_source.is_file():
            shutil.copy2(replay_source, sample_output / "native_replay.replay")

    public_summary = _public_summary(summary)
    (reports_output / "summary.json").write_text(
        json.dumps(public_summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    shutil.copy2(reports_root / "summary.md", reports_output / "summary.md")
    for plot in PLOT_NAMES:
        source = reports_root / plot
        if not source.is_file():
            raise ValueError(f"report artifact is missing: {source}")
        shutil.copy2(source, reports_output / plot)

    generated_at = generated_at or datetime.now(UTC)
    compact_summary = {
        "episode_count": int(summary.get("episode_count", 0)),
        "valid_episode_count": int(summary.get("valid_episode_count", 0)),
        "error_count": int(summary.get("error_count", 0)),
        "warning_count": int(summary.get("warning_count", 0)),
        "total_bytes": int(summary.get("total_bytes", 0)),
        "missing_capture_frames": int(summary.get("missing_capture_frames", 0)),
        "capture_dropped": int(summary.get("capture_dropped", 0)),
        "modes": summary.get("modes", {}),
        "outcomes": summary.get("outcomes", {}),
        "replay_comparison_count": len(summary.get("replay_comparisons", [])),
        "performance": summary.get("performance", {}),
        "combat": summary.get("combat", {}),
    }
    evidence: dict[str, Any] = {
        "schema_version": 2,
        "generated_utc": generated_at.astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        ),
        "source_episode_id": manifest["episode_id"],
        "source_sim_frame": int(sample_row["sim_frame"]),
        "git_revision": str(manifest["git_revision"]),
        "course_hash": str(manifest["course_hash"]),
        "source_shots_fired": int(manifest.get("shots_fired", 0)),
        "source_shots_hit": int(manifest.get("shots_hit", 0)),
        "source_combat_sim_frame": combat_frame,
        "summary": compact_summary,
        "video_duration_s": None,
        "artifacts_sha256": {},
    }
    if create_video:
        evidence["video_duration_s"] = _create_video(
            captured_episodes,
            reports_root,
            output_root,
            summary,
            evidence["git_revision"],
        )

    ml_manifest = _publish_ml_dataset_manifest(episodes_root, output_root)
    if ml_manifest is not None:
        evidence["ml_dataset"] = {
            "episode_count": ml_manifest["episode_count"],
            "transition_count": ml_manifest["transition_count"],
            "sensor_policy_sample_count": ml_manifest[
                "sensor_policy_sample_count"
            ],
        }
    _write_evidence_readme(
        output_root,
        evidence,
        has_video=(output_root / VIDEO_NAME).is_file(),
        has_ml_manifest=ml_manifest is not None,
    )
    artifact_files = sorted(
        path
        for path in output_root.rglob("*")
        if path.is_file() and path.name != "evidence.json"
    )
    evidence["artifacts_sha256"] = {
        path.relative_to(output_root).as_posix(): _sha256(path)
        for path in artifact_files
    }
    (output_root / "evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return evidence


def publish_evidence(
    episodes_root: Path,
    reports_root: Path,
    output_root: Path,
    *,
    create_video: bool = True,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    output_root = output_root.absolute()
    if output_root.is_symlink():
        raise ValueError(f"output root must not be a symlink: {output_root}")
    output_root = output_root.resolve()
    current_directory = Path.cwd().resolve()
    if output_root == current_directory or output_root in current_directory.parents:
        raise ValueError("output root must not contain the working directory")
    if output_root == Path(output_root.anchor):
        raise ValueError("output root must not be a filesystem root")
    if output_root.exists() and not output_root.is_dir():
        raise ValueError(f"output root is not a directory: {output_root}")

    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_root.name}-",
        dir=output_root.parent,
    ) as temporary:
        transaction_root = Path(temporary)
        staged_output = transaction_root / "bundle"
        evidence = _build_evidence_bundle(
            episodes_root,
            reports_root,
            staged_output,
            create_video=create_video,
            generated_at=generated_at,
        )

        previous_output = transaction_root / "previous"
        had_previous_output = output_root.exists()
        if had_previous_output:
            output_root.replace(previous_output)
        try:
            staged_output.replace(output_root)
        except BaseException:
            if had_previous_output and previous_output.exists():
                previous_output.replace(output_root)
            raise
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish a compact, tracked evidence bundle from SimTrace output"
    )
    parser.add_argument(
        "episodes_root",
        nargs="?",
        type=Path,
        default=Path("Saved/SimTrace/episodes"),
    )
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=Path("Saved/SimTrace/reports"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("docs/evidence"),
    )
    parser.add_argument("--skip-video", action="store_true")
    arguments = parser.parse_args(argv)
    evidence = publish_evidence(
        arguments.episodes_root,
        arguments.reports_root,
        arguments.output_root,
        create_video=not arguments.skip_video,
    )
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
