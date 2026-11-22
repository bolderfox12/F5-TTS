"""Find active audio windows missing from an initial transcript."""

import numpy as np


def recovery_windows(samples, rate, segments):
    duration = len(samples) / rate
    uncovered = []
    cursor = 0.0
    for row in segments:
        if row["start"] - cursor >= 0.55:
            uncovered.append((cursor, row["start"]))
        cursor = max(cursor, row["end"])
    if duration - cursor >= 0.55:
        uncovered.append((cursor, duration))

    step = max(1, round(rate * 0.08))
    windows = []
    for start, end in uncovered:
        first, last = round(start * rate), round(end * rate)
        frames = samples[first:last]
        frame_count = len(frames) // step
        if frame_count < 2:
            continue
        levels = np.sqrt(np.mean(np.square(frames[: frame_count * step].reshape(-1, step)), axis=1))
        peak = float(levels.max())
        if peak < 0.0015 or peak < float(np.median(levels)) * 1.2:
            continue
        active = np.flatnonzero(levels >= max(0.001, peak * 0.075))
        if len(active) < 2:
            continue
        groups = np.split(active, np.flatnonzero(np.diff(active) > 7) + 1)
        for group in groups:
            if len(group) < 2:
                continue
            begin = max(start, start + group[0] * step / rate - 0.16)
            finish = min(end, start + (group[-1] + 1) * step / rate + 0.20)
            while finish - begin > 18:
                windows.append((float(begin), float(begin + 18)))
                begin += 18
            if finish - begin >= 0.25:
                windows.append((float(begin), float(finish)))
    return windows


def merge_recovered(segments, recovered, windows):
    ordered = sorted(recovered, key=lambda row: row["start"])
    result = list(segments)
    window_index = 0
    for row in ordered:
        while window_index < len(windows) and windows[window_index][1] <= row["start"]:
            window_index += 1
        if window_index >= len(windows):
            break
        start, end = windows[window_index]
        if start - 0.12 <= row["start"] < end and start < row["end"] <= end + 0.12:
            first, last = max(start, row["start"]), min(end, row["end"])
            if last - first >= 0.12:
                result.append({**row, "start": first, "end": last, "recovered": True})
    return sorted(result, key=lambda row: row["start"])
