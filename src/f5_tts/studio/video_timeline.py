"""Playback timeline shared by the source and edited video players."""

from nicegui.element import Element


class VideoTimeline(Element, component="video_timeline.js"):
    def __init__(self, t, original, dubbed=None):
        super().__init__()
        self._props.update(
            original=original.id,
            dubbed=dubbed.id if dubbed is not None else None,
            originalTracks=[],
            dubbedTracks=[],
            labels={
                "timeline": t("Linha do tempo do vídeo"),
                "original": t("Vídeo original"),
                "dubbed": t("Vídeo dublado"),
                "video": t("Vídeo"),
            },
        )

    def set_tracks(self, *, original=None, dubbed=None):
        if original is not None:
            self._props["originalTracks"] = original
        if dubbed is not None:
            self._props["dubbedTracks"] = dubbed
