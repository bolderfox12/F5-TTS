"""Playback timeline shared by the source and edited video players."""

from nicegui.element import Element


class VideoTimeline(Element, component="video_timeline.js"):
    def __init__(self, t, original, dubbed=None, *, select_range=False, on_range=None):
        super().__init__()
        self._props.update(
            original=original.id,
            dubbed=dubbed.id if dubbed is not None else None,
            originalTracks=[],
            dubbedTracks=[],
            rangeEnabled=select_range,
            rangeStart=None,
            rangeEnd=None,
            labels={
                "timeline": t("Linha do tempo do vídeo"),
                "original": t("Vídeo original"),
                "dubbed": t("Vídeo dublado"),
                "video": t("Vídeo"),
                "dubRange": t("Trecho para dublar"),
                "rangeHint": t("Arraste as alças para escolher o trecho. Arraste a faixa para criar uma nova seleção."),
            },
        )
        if on_range is not None:
            self.on("range-change", on_range)

    def set_tracks(self, *, original=None, dubbed=None):
        if original is not None:
            self._props["originalTracks"] = original
        if dubbed is not None:
            self._props["dubbedTracks"] = dubbed

    def set_range(self, selected):
        start, end = selected if selected else (None, None)
        self._props.update(rangeStart=start, rangeEnd=end)
