"""Interactive timeline for the advanced video editor."""

from nicegui.element import Element
from f5_tts.studio.montage import video_tracks


class MontageTimeline(Element, component="montage_timeline.js"):
    def __init__(
        self,
        t,
        player,
        on_select,
        on_move,
        on_seek,
        on_split,
        on_duplicate,
        on_audio_effects,
        on_video_effects,
        on_transitions,
    ):
        super().__init__()
        self._props.update(
            player=player.id,
            clips=[],
            selected="",
            length=10,
            tracks=["V1", "V2", "A1", "A2", "TX", "CC"],
            labels={
                "timeline": t("Linha do tempo da montagem"),
                "position": t("Posição na linha do tempo"),
                "zoom": t("Zoom"),
                "V1": t("Vídeo principal"),
                "V2": t("Vídeo sobreposto"),
                "A1": t("Voz e áudio"),
                "A2": t("Música e efeitos"),
                "CC": t("Legendas"),
                "TX": t("Texto"),
                "duplicate": t("Duplicar clipe"),
                "duplicateTrack": t("Duplicar faixa"),
                "audioEffects": t("Efeitos de áudio"),
                "transitions": t("Transições"),
                "videoEffects": t("Efeitos de vídeo"),
                "audioTrack": t("Áudio"),
                "fx": t("Efeitos"),
                "shortcut": t("S · dividir no cursor"),
            },
        )
        self.on("select-clip", on_select)
        self.on("move-clip", on_move)
        self.on("seek", on_seek)
        self.on("split", on_split)
        self.on("duplicate", on_duplicate)
        self.on("audio-effects", on_audio_effects)
        self.on("video-effects", on_video_effects)
        self.on("transitions", on_transitions)

    def update_clips(self, clips, selected, length, audio_tracks):
        self._props.update(
            clips=[
                {
                    **{key: clip[key] for key in ("id", "name", "start", "track", "in", "out", "has_audio", "kind")},
                    "transition": (
                        clip.get("video_transition", {}).get("kind", "none") != "none"
                        or any(clip.get("audio_transition", {}).get(key, 0) for key in ("fade_in", "fade_out"))
                    ),
                    "fx": any(effect.get("enabled") for effect in clip.get("video_effects", {}).values()),
                }
                for clip in clips
            ],
            selected=selected or "",
            length=max(10, length + 2),
            tracks=[*video_tracks(clips), *audio_tracks, "TX", "CC"],
        )
