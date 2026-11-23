"""Timing and FFmpeg filters for montage transitions."""

import math

VIDEO_TRANSITIONS = {
    "none": "Sem transição",
    "dissolve": "Dissolução",
    "black": "Entrada a partir do preto",
    "slide": "Deslizar da direita",
}
AUDIO_CURVES = {"tri": "Linear", "qsin": "Potência constante"}


def validate_transitions(clip):
    video = clip.get("video_transition", {})
    audio = clip.get("audio_transition", {})
    if not isinstance(video, dict) or video.get("kind", "none") not in VIDEO_TRANSITIONS:
        raise ValueError("Escolha uma transição de vídeo válida.")
    if not isinstance(audio, dict) or audio.get("curve", "tri") not in AUDIO_CURVES:
        raise ValueError("Escolha uma curva de áudio válida.")
    durations = (video.get("duration", 0), audio.get("fade_in", 0), audio.get("fade_out", 0))
    if any(
        not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 10 for value in durations
    ):
        raise ValueError("A duração da transição deve ficar entre 0 e 10 segundos.")


def clip_length(clip):
    return clip["out"] - clip["in"]


def overlap_start(clips, clip, seconds):
    previous = max(
        (
            row
            for row in clips
            if row["track"] == clip["track"] and row["start"] < clip["start"] and row["id"] != clip["id"]
        ),
        key=lambda row: row["start"],
        default=None,
    )
    if previous is None:
        raise ValueError("Adicione outro clipe antes deste na mesma faixa para criar a transição.")
    length = min(seconds, clip_length(clip), clip_length(previous) - 0.01)
    if length < 0.01:
        raise ValueError("Os clipes são curtos demais para essa transição.")
    return previous, round(previous["start"] + clip_length(previous) - length, 3), length


def audio_fades(clip):
    config = clip.get("audio_transition", {})
    length = clip_length(clip)
    fade_in = min(config.get("fade_in", 0), length)
    fade_out = min(config.get("fade_out", 0), length)
    curve = config.get("curve", "tri")
    return [
        f"afade=t={direction}:st={start:.6f}:d={duration:.6f}:curve={curve}"
        for direction, start, duration in (("in", 0, fade_in), ("out", length - fade_out, fade_out))
        if duration > 0
    ]


def video_transition(clip, base_x, base_y):
    config = clip.get("video_transition", {})
    kind = config.get("kind", "none")
    duration = min(config.get("duration", 0), clip_length(clip))
    if kind == "none" or duration <= 0:
        return [], f"{base_x}:{base_y}"
    if kind == "dissolve":
        return ["format=yuva420p", f"fade=t=in:st=0:d={duration:.6f}:alpha=1"], f"{base_x}:{base_y}"
    if kind == "black":
        return [f"fade=t=in:st=0:d={duration:.6f}"], f"{base_x}:{base_y}"
    start = clip["start"]
    x = f"({base_x})+(W-({base_x}))*max(0,1-(t-{start:.6f})/{duration:.6f})"
    return [], f"x='{x}':y={base_y}:eval=frame"
