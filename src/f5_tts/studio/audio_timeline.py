"""Audio player with a synchronized waveform and optional trim controls."""

from nicegui import run
from nicegui.elements.mixins.source_element import SourceElement


class AudioTimeline(SourceElement, component="audio_timeline.js"):
    SOURCE_IS_MEDIA_FILE = True

    def __init__(self, t, on_trim=None):
        super().__init__(source="")
        self._props.update(
            peaks=[],
            duration=0,
            trimmable=on_trim is not None,
            trimming=False,
            labels={
                "timeline": t("Linha do tempo do áudio"),
                "start": t("Início do recorte"),
                "end": t("Fim do recorte"),
                "selection": t("Duração do recorte"),
                "preview": t("Ouvir trecho"),
                "stop": t("Parar trecho"),
                "apply": t("Usar só este trecho"),
                "hint": t("Arraste as marcas de início e fim para recortar de 1 a 30 segundos."),
            },
        )
        if on_trim is not None:
            self.on("trim", on_trim)

    def set_source(self, source):
        if source != self.source:
            with self._props.suspend_updates():
                self._props.update(peaks=[], duration=0, trimming=False)
                super().set_source(source)
        return self

    def set_waveform(self, waveform):
        self._props.update(peaks=waveform["peaks"], duration=waveform["duration"])

    def set_trimming(self, value):
        self._props["trimming"] = value

    def matches_trim(self, selection):
        return isinstance(selection, dict) and selection.get("src") == self._props["src"]

    async def load(self, source, measure):
        self.set_source(source)
        try:
            waveform = await run.io_bound(measure, source)
        except (OSError, RuntimeError, ValueError):
            return
        if self.source == source and not self.is_deleted:
            self.set_waveform(waveform)
