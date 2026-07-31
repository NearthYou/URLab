# Recorded evidence

This directory contains a compact, tracked extract of a real SimTrace run.
The full generated dataset remains under `Saved/SimTrace` and is ignored by Git.

- Source episode: `episode_20260731T091927349Z_bot_s5150_00`
- Git revision recorded by Unreal: `4a349dc95d0f`
- Valid episodes: 40
- Missing captures: 0
- Capture drops: 0
- Replay comparisons: 7
- Source combat ledger: 1 shot, 1 hit

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
- [Combat ledger plot](reports/combat_ledger.png)
- [Replay error plot](reports/replay_error.png)
- [Capture performance plot](reports/capture_performance.png)

## 60-second evidence reel

[Open the MP4](simtrace_demo.mp4)

The reel is generated only from recorded RGB frames and report artifacts.

The compact sample and evidence reel use deterministic bot episodes.
The validation report also includes 10 human-play episodes, labeled by mode.
