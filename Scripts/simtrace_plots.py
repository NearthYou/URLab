from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def bot_frame_times_by_capture(
    results: list[dict[str, Any]],
) -> tuple[list[float], list[float]]:
    capture_on = [
        value
        for result in results
        if result["mode"] == "bot" and result.get("_capture_hz", 0) > 0
        for value in result["_frame_times_ms"]
    ]
    capture_off = [
        value
        for result in results
        if result["mode"] == "bot" and result.get("_capture_hz", 0) == 0
        for value in result["_frame_times_ms"]
    ]
    return capture_on, capture_off


def write_plots(
    output: Path,
    results: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> list[str]:
    plot_paths: list[str] = []
    episode_labels = [
        f"{index:02d} {result['mode']} s{result['seed']}"
        for index, result in enumerate(results, start=1)
    ]
    sizes_mb = [result["total_bytes"] / (1024 * 1024) for result in results]

    plt.figure(figsize=(max(8, len(results) * 0.4), 4.5))
    plt.bar(range(len(results)), sizes_mb, color="#277da1")
    plt.xticks(
        range(len(results)), episode_labels, rotation=55, ha="right", fontsize=7
    )
    plt.ylabel("MiB")
    plt.title("Episode sizes")
    plt.tight_layout()
    path = output / "episode_sizes.png"
    plt.savefig(path, dpi=150)
    plt.close()
    plot_paths.append(path.name)

    outcomes: dict[str, int] = {}
    for result in results:
        end_reason = str(result["end_reason"])
        outcomes[end_reason] = outcomes.get(end_reason, 0) + 1
    plt.figure(figsize=(7, 4))
    plt.bar(outcomes.keys(), outcomes.values(), color="#43aa8b")
    plt.ylabel("Episodes")
    plt.title("Episode outcomes")
    plt.tight_layout()
    path = output / "episode_outcomes.png"
    plt.savefig(path, dpi=150)
    plt.close()
    plot_paths.append(path.name)

    plt.figure(figsize=(8, 4.5))
    plotted = False
    action_bins = np.linspace(0.0, math.sqrt(2.0) + 1e-6, 31)
    for mode in ("human", "bot", "input-replay"):
        values = [
            value
            for result in results
            if result["mode"] == mode
            for value in result["_move_magnitudes"]
        ]
        if values:
            weights = np.full(len(values), 100.0 / len(values))
            plt.hist(
                values,
                bins=action_bins,
                weights=weights,
                alpha=0.45,
                label=mode,
            )
            plotted = True
    if plotted:
        plt.legend()
    plt.xlabel("Move input magnitude")
    plt.ylabel("Samples percent")
    plt.title("Action distributions")
    plt.tight_layout()
    path = output / "action_distributions.png"
    plt.savefig(path, dpi=150)
    plt.close()
    plot_paths.append(path.name)

    plt.figure(figsize=(8, 4.5))
    if comparisons:
        labels = [f"seed {item['seed']}" for item in comparisons]
        x = np.arange(len(labels))
        plt.bar(
            x - 0.2,
            [item["mean_position_error_cm"] for item in comparisons],
            width=0.4,
            label="mean",
        )
        plt.bar(
            x + 0.2,
            [item["p95_position_error_cm"] for item in comparisons],
            width=0.4,
            label="p95",
        )
        plt.xticks(x, labels, rotation=60, ha="right", fontsize=7)
        plt.legend()
    plt.ylabel("Position error cm")
    plt.title("JSON input replay error")
    plt.tight_layout()
    path = output / "replay_error.png"
    plt.savefig(path, dpi=150)
    plt.close()
    plot_paths.append(path.name)

    capture_on, capture_off = bot_frame_times_by_capture(results)
    plt.figure(figsize=(7, 4.5))
    data: list[list[float]] = []
    labels: list[str] = []
    if capture_off:
        data.append(capture_off)
        labels.append("capture off")
    if capture_on:
        data.append(capture_on)
        labels.append("capture on")
    if data:
        plt.boxplot(data, tick_labels=labels, showfliers=False)
    plt.ylabel("Frame time ms")
    plt.title("Bot capture performance")
    plt.tight_layout()
    path = output / "capture_performance.png"
    plt.savefig(path, dpi=150)
    plt.close()
    plot_paths.append(path.name)
    return plot_paths
