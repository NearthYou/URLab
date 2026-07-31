# Recorded evidence

This directory contains a compact, tracked extract of a real SimTrace run.
The full generated dataset remains under `Saved/SimTrace` and is ignored by Git.

- Source episode: `episode_20260730T164634194Z_bot_s1000_00`
- Git revision recorded by Unreal: `ff1b9a7bca35`
- Valid episodes: 35
- Missing captures: 0
- Capture drops: 0
- Replay comparisons: 5

## Runtime view

![First-person RGB captured during the run](runtime_first_person.png)

## Synchronized RGB and depth

![RGB and depth from the same simulation frame](rgb_depth_pair.png)

Raw files are in [`sample`](sample), including the original 16-bit depth PNG,
the episode manifest, a trajectory excerpt, and the native Replay archive when
one was present.

## Reports

- [Validation summary](reports/summary.md)
- [Machine-readable summary](reports/summary.json)
- [Replay error plot](reports/replay_error.png)
- [Capture performance plot](reports/capture_performance.png)

## 60-second evidence reel

[Open the MP4](simtrace_demo.mp4)

The reel is generated only from recorded RGB frames and report artifacts.

The compact sample and evidence reel use deterministic bot episodes.
The validation report also includes 10 human-play episodes, labeled by mode.
