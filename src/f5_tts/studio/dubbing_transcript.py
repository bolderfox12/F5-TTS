"""Checks for repeated phrases and duplicate dubbing cards."""

import re
import unicodedata
from collections import Counter


def normalized_words(text):
    folded = unicodedata.normalize("NFKC", text).casefold()
    return tuple(re.findall(r"[^\W_]+", folded))


def repeated_text(text):
    words = normalized_words(text)
    for size in range(1, min(7, len(words))):
        minimum = 2 if size >= 3 else 3
        for start in range(len(words) - size * minimum + 1):
            unit = words[start : start + size]
            if words[start : start + size * minimum] == unit * minimum:
                return True
    if len(words) >= 7:
        count = Counter(words).most_common(1)[0][1]
        return count >= 4 and count * 5 >= len(words) * 2
    return False


def needs_translation_review(row, text, issue=""):
    approval = [text.strip(), row["text"], row["start"], row["end"]]
    return row.get("approved_repetition") != approval and (repeated_text(text) or issue in ("duplicate", "nearby"))


def same_timed_line(first, second):
    if first["start"] is None or second["start"] is None or first["end"] is None or second["end"] is None:
        return False
    if abs(first["start"] - second["start"]) > 0.25 or abs(first["end"] - second["end"]) > 0.35:
        return False
    duration = min(first["end"] - first["start"], second["end"] - second["start"])
    overlap = min(first["end"], second["end"]) - max(first["start"], second["start"])
    if duration <= 0 or overlap <= duration / 2:
        return False
    words = normalized_words(first["text"])
    return bool(words) and words == normalized_words(second["text"])


def transcript_warnings(rows):
    """Keep genuine repetitions at distinct times available for review."""
    indexed = sorted(
        ((index, row) for index, row in enumerate(rows) if row["start"] is not None and row["end"] is not None),
        key=lambda item: item[1]["start"],
    )
    warnings = ["repeat" if repeated_text(row["text"]) else "" for row in rows]
    previous = None
    for index, row in indexed:
        if previous is not None:
            if same_timed_line(previous, row):
                warnings[index] = "duplicate"
                continue
            words = normalized_words(row["text"])
            if (
                len(words) >= 3
                and row["start"] - previous["end"] <= 0.5
                and words == normalized_words(previous["text"])
            ):
                warnings[index] = "nearby"
        previous = row
    return warnings
