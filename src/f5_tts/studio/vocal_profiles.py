"""Conservative acoustic voice profiles for reviewing dubbing dialogue."""

from collections import Counter, defaultdict

import numpy as np
import soundfile as sf


PROFILE_LABELS = {"masculine": "Masculina", "feminine": "Feminina", "unknown": "Indefinida"}
PROFILE_ICONS = {"masculine": "man", "feminine": "woman", "unknown": "person_outline"}


def estimate_profile(path):
    samples, rate = sf.read(path, frames=64000, dtype="float32")
    return estimate_samples(samples, rate)


def estimate_samples(samples, rate):
    import librosa

    if len(samples) < rate // 3 or not np.any(samples):
        return "unknown"
    pitch, voiced, confidence = librosa.pyin(samples, sr=rate, fmin=65, fmax=420, frame_length=1024, hop_length=256)
    valid = pitch[voiced & (confidence >= 0.55)]
    if len(valid) < 12:
        valid = pitch[voiced & (confidence >= 0.40)]
    if len(valid) < 12:
        return "unknown"
    low, high = np.quantile(valid, [0.35, 0.65])
    if high <= 155:
        return "masculine"
    if low >= 175:
        return "feminine"
    return "unknown"


def profile_check_status(row):
    check = row.get("vocal_check")
    if not check or (check["start"], check["end"]) != (row["start"], row["end"]):
        return None
    if check["profile"] == "unknown":
        return "unclear"
    return "match" if check["profile"] == row.get("vocal_profile") else "mismatch"


def keep_distinct_profiles(labels, profiles, durations):
    """Separate strongly different profiles within any proposed speaker group."""
    coverage = defaultdict(Counter)
    for label, profile, duration in zip(labels, profiles, durations, strict=True):
        if profile != "unknown":
            coverage[label][profile] += duration
    groups = {}
    result = []
    for label, profile in zip(labels, profiles, strict=True):
        known = coverage[label]
        if len(known) > 1:
            subgroup = profile if profile != "unknown" else known.most_common(1)[0][0]
        else:
            subgroup = ""
        result.append(groups.setdefault((label, subgroup), len(groups) + 1))
    return result
