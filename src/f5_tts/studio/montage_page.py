"""Workspace library, project transfers and the advanced montage UI."""

import re
from pathlib import Path
from uuid import uuid4

from nicegui import ui

from f5_tts.studio.audio_cleanup import audio_cleanup_handoff_url
from f5_tts.studio.local_tools import subtitles
from f5_tts.studio.montage import (
    EQ_BANDS,
    EQ_LIMIT,
    PITCH_LIMIT,
    MontageStore,
    TRACKS,
    asset_kind,
    audio_tracks,
    duplicate_clip,
    duplicate_track,
    linked_clips,
    new_audio_track,
    render_montage,
    sequence_duration,
    separate_video_audio,
    split_at,
    validate_clip,
    video_tracks,
)
from f5_tts.studio.montage_assistant_panel import assistant_dialog
from f5_tts.studio.montage_fx import FX_CONTROLS, FX_TITLES, GRADIENT_COLORS
from f5_tts.studio.montage_timeline import MontageTimeline
from f5_tts.studio.montage_transitions import AUDIO_CURVES, VIDEO_TRANSITIONS, overlap_start
from f5_tts.studio.video_dubbing import VIDEO_EXTENSIONS, VIDEO_LIMIT
from f5_tts.studio.video_editor import MUSIC_EXTENSIONS

EDITOR_PATH = "/video-editor"


def send_to_video_editor(service, action, assets, name):
    t = service.translate_ui
    store = MontageStore(service)
    with ui.dialog() as dialog, ui.card().classes("studio-card w-full max-w-md"):
        ui.label(t("Enviar ao editor de vídeo")).classes("font-semibold text-lg")
        projects = {"": t("Novo projeto de vídeo"), **{row["id"]: row["name"] for row in store.list()}}
        destination = ui.select(projects, value="", label=t("Projeto de vídeo")).props("outlined").classes("w-full")
        title = ui.input(t("Nome do projeto"), value=name).props("outlined").classes("w-full")
        title.bind_visibility_from(destination, "value", lambda value: not value)

        async def send():
            button.disable()
            try:
                project = await action(store.transfer, destination.value, title.value or "", assets)
                if project:
                    dialog.close()
                    ui.navigate.to(f"{EDITOR_PATH}?project_id={project['id']}")
            finally:
                button.enable()

        button = ui.button(t("Abrir no editor"), icon="movie", on_click=send)
    dialog.open()


def register_montage_page(service, shell, action):
    store = MontageStore(service)
    t = service.translate_ui

    @ui.page(EDITOR_PATH)
    def editor_page(project_id: str = ""):
        ui.add_css("""
            .video-fx-scroll { scrollbar-color: #617e86 #e6eef0; }
            .video-fx-scroll::-webkit-scrollbar { height: 10px; }
            .video-fx-scroll::-webkit-scrollbar-track { background: #e6eef0; border-radius: 5px; }
            .video-fx-scroll::-webkit-scrollbar-thumb { background: #617e86; border-radius: 5px; }
        """)
        current = None
        selected = None
        playhead = 0
        pending_seek = None
        busy = False
        library = []

        def reload_projects():
            projects.set_options(
                {row["id"]: row["name"] for row in store.list()}, value=current["id"] if current else None
            )

        def set_result(video=None, captions=None, *, seek=None):
            nonlocal pending_seek
            pending_seek = seek
            result.clear()
            player.set_visibility(video is not None)
            empty_player.set_visibility(video is None)
            if not video:
                player.pause()
                return
            player.set_source(video)
            with result:
                ui.button(t("Baixar MP4"), icon="download", on_click=lambda path=video: ui.download.file(path))
                ui.button(
                    t("Enviar para limpar áudio"),
                    icon="graphic_eq",
                    on_click=lambda path=video: ui.navigate.to(audio_cleanup_handoff_url(service, video=path)),
                ).props("outline")
                if captions:
                    ui.button(t("Baixar SRT"), icon="subtitles", on_click=lambda: ui.download.file(captions))

        def seek_new_preview():
            nonlocal pending_seek
            if pending_seek is not None:
                position = pending_seek
                pending_seek = None
                timeline.run_method("seek", position)

        def show_project():
            nonlocal playhead
            playhead = 0
            name.set_value(current["name"] if current else "")
            profile.set_value(current["profile"] if current else "720p")
            set_result()
            redraw()
            timeline.run_method("seek", 0)

        def redraw():
            clips = current["clips"] if current else []
            timeline.update_clips(clips, selected, sequence_duration(clips), audio_tracks(current or {"clips": []}))
            inspector.refresh()

        def set_active(event):
            nonlocal selected
            if not busy:
                selected = event.args
                redraw()

        def position(event):
            nonlocal playhead
            playhead = event.args

        async def persist(candidate, new_selected=None):
            nonlocal current, selected
            saved = await action(store.save, candidate)
            if saved:
                current = saved
                selected = new_selected
                set_result()
                redraw()
            return saved

        async def ensure_project():
            nonlocal current
            if current is None:
                current = await action(store.create, name.value or "")
                if current:
                    reload_projects()
                    show_project()
            return current

        async def add(source, label, *, at=None):
            nonlocal busy
            if busy:
                return
            busy = True
            try:
                await insert_clip(source, label, at)
            finally:
                busy = False

        async def insert_clip(source, label, at=None):
            project = await ensure_project()
            if not project:
                return
            clips = await action(store.import_clips, source, label, playhead if at is None else at)
            if clips:
                await persist({**project, "clips": [*project["clips"], *clips]}, clips[0]["id"])

        async def upload(event):
            nonlocal busy
            if busy:
                upload_box.reset()
                return
            file = event.file
            extension = Path(file.name).suffix.lower()
            if extension not in (*VIDEO_EXTENSIONS, *MUSIC_EXTENSIONS, ".srt") or file.size() > VIDEO_LIMIT:
                upload_box.reset()
                return ui.notify(t("Envie um vídeo, áudio ou SRT de até 500 MB."), type="warning")
            busy = True
            path = service.uploads / f"{uuid4().hex}{extension}"
            try:
                await file.save(path)
                await insert_clip(path, file.name, playhead)
                await refresh_library()
            except Exception as error:
                path.unlink(missing_ok=True)
                ui.notify(t(str(error)), type="negative")
            finally:
                busy = False
                upload_box.reset()

        def add_caption():
            with ui.dialog() as dialog, ui.card().classes("studio-card w-full max-w-md"):
                ui.label(t("Adicionar legenda")).classes("font-semibold text-lg")
                text = ui.textarea(t("Texto da legenda")).props("outlined").classes("w-full")
                length = ui.number(t("Duração da legenda (s)"), value=2, min=0.1, max=60, step=0.1).props("outlined")

                async def insert():
                    nonlocal busy
                    if busy:
                        return
                    value = " ".join((text.value or "").split())
                    if not value or length.value is None or not 0.1 <= length.value <= 60:
                        return ui.notify(t("Preencha o texto e a duração da legenda."), type="warning")
                    busy = True
                    path = service.uploads / f"{uuid4().hex}.srt"
                    try:
                        path.write_text(
                            subtitles([{"start": 0, "end": float(length.value), "text": value}]), encoding="utf-8"
                        )
                        await insert_clip(path, value[:80])
                        dialog.close()
                    finally:
                        busy = False

                ui.button(t("Adicionar no cursor"), icon="add", on_click=insert)
            dialog.open()

        def add_title():
            with ui.dialog() as dialog, ui.card().classes("studio-card w-full max-w-md"):
                ui.label(t("Adicionar texto")).classes("font-semibold text-lg")
                text = ui.textarea(t("Texto na tela")).props("outlined autofocus").classes("w-full")
                length = ui.number(t("Duração do texto (s)"), value=3, min=0.1, max=120, step=0.1).props("outlined")

                async def insert():
                    nonlocal busy
                    if busy:
                        return
                    value = (text.value or "").strip()
                    if not value or length.value is None or not 0.1 <= length.value <= 120:
                        return ui.notify(t("Preencha o texto e informe uma duração válida."), type="warning")
                    busy = True
                    try:
                        project = await ensure_project()
                        if project:
                            clip = {
                                "id": uuid4().hex,
                                "name": value.splitlines()[0][:80],
                                "text": value,
                                "kind": "text",
                                "track": "TX",
                                "duration": length.value,
                                "has_audio": False,
                                "start": playhead,
                                "in": 0,
                                "out": length.value,
                                "volume": 100,
                                "font_size": 48,
                            }
                            if await persist({**project, "clips": [*project["clips"], clip]}, clip["id"]):
                                dialog.close()
                    finally:
                        busy = False

                ui.button(t("Adicionar no cursor"), icon="add", on_click=insert)
            dialog.open()

        async def refresh_library():
            found = await action(store.inventory)
            if found is not None:
                library[:] = found
                library_options()

        def library_options():
            selected_kind = category.value
            assets.set_options(
                {
                    row["path"]: (row["uses"][0] + " · " if row.get("uses") else row["category"] + " · ") + row["name"]
                    for row in library
                    if not selected_kind or asset_kind(row["path"]) == selected_kind
                }
            )

        def asset_label(path):
            item = next((row for row in library if row["path"] == path), None)
            uses = item.get("uses") if item else None
            return uses[0] if uses else Path(path).name

        async def add_selected():
            if assets.value:
                await add(assets.value, asset_label(assets.value))

        def inspect_asset():
            if not assets.value:
                return
            path = assets.value
            with ui.dialog() as dialog, ui.card().classes("studio-card w-full max-w-3xl"):
                ui.label(asset_label(path)).classes("text-lg")
                match asset_kind(path):
                    case "video":
                        ui.video(path).classes("w-full")
                    case "audio":
                        ui.audio(path).classes("w-full")
                    case "caption":
                        ui.label(t("Legenda SRT")).classes("studio-muted")
                ui.button(t("Fechar"), on_click=dialog.close).props("flat")
            dialog.open()

        async def new_project():
            nonlocal current, selected, busy
            if busy:
                return
            busy = True
            try:
                project = await action(store.create, name.value or "")
                if project:
                    current, selected = project, None
                    reload_projects()
                    show_project()
            finally:
                busy = False

        async def open_project():
            nonlocal current, selected, busy
            if busy or not projects.value:
                return
            busy = True
            try:
                project = await action(store.open, projects.value)
                if project:
                    current, selected = project, None
                    show_project()
            finally:
                busy = False

        async def save_settings():
            nonlocal busy
            if busy:
                return
            busy = True
            try:
                if await ensure_project():
                    changed = {
                        **current,
                        "name": (name.value or "").strip()[:100] or t("Meu vídeo"),
                        "profile": profile.value,
                    }
                    if await persist(changed, selected):
                        reload_projects()
                        ui.notify(t("Projeto de vídeo salvo."), type="positive")
            finally:
                busy = False

        async def replace_clip(clip, *, related=None, **values):
            nonlocal busy
            if busy or not current:
                return
            timing = {key: value for key, value in values.items() if key in {"start", "in", "out"}}
            members = linked_clips(current["clips"], clip)
            edits = {row["id"]: {**row, **(values if row["id"] == clip["id"] else timing)} for row in members}
            for row in current["clips"]:
                if related and row["id"] in related:
                    edits[row["id"]] = {**edits.get(row["id"], row), **related[row["id"]]}
            try:
                for edited in edits.values():
                    validate_clip(edited)
            except ValueError as error:
                ui.notify(t(str(error)), type="warning")
                redraw()
                return
            busy = True
            try:
                return await persist(
                    {**current, "clips": [edits.get(row["id"], row) for row in current["clips"]]},
                    clip["id"],
                )
            finally:
                busy = False

        async def move_clip(event):
            if current:
                clip = next((row for row in current["clips"] if row["id"] == event.args["id"]), None)
                if clip:
                    await replace_clip(clip, start=event.args["start"])

        async def split_clip(identifier=None, cursor=None):
            nonlocal busy
            if busy or not current:
                return
            if cursor is None:
                cursor = playhead
            clips = current["clips"]

            def contains(row):
                return row["start"] < cursor < row["start"] + row["out"] - row["in"]

            clip = next((row for row in clips if row["id"] == identifier and contains(row)), None)
            if clip is None:
                candidates = [row for row in clips if contains(row)]
                clip = next((row for row in reversed(candidates) if row["kind"] == "video"), None)
                clip = clip or (candidates[-1] if candidates else None)
            if clip is None:
                return ui.notify(t("Posicione o cursor dentro do clipe para dividir."), type="warning")
            try:
                updated, right = split_at(clips, clip, cursor)
            except ValueError as error:
                return ui.notify(t(str(error)), type="warning")
            busy = True
            try:
                await persist({**current, "clips": updated}, right)
            finally:
                busy = False

        async def key_split(event):
            await split_clip(event.args["id"], event.args["position"])

        async def add_audio_track():
            nonlocal busy
            if busy:
                return
            busy = True
            try:
                project = await ensure_project()
                if project:
                    await persist(
                        {**project, "audio_tracks": [*audio_tracks(project), new_audio_track(project)]}, selected
                    )
            finally:
                busy = False

        async def add_background_music():
            nonlocal busy
            if busy or not current or not assets.value:
                return
            try:
                clip = await action(store.clip, assets.value, asset_label(assets.value), 0)
                if not clip or clip["kind"] != "audio":
                    return ui.notify(t("Selecione uma música na biblioteca."), type="warning")
                video_end = max(
                    (row["start"] + row["out"] - row["in"] for row in current["clips"] if row["kind"] == "video"),
                    default=0,
                )
                target_duration = video_end or sequence_duration(current["clips"]) or clip["duration"]
                target_track = new_audio_track(current)
                updated = {
                    **clip,
                    "track": target_track,
                    "start": 0,
                    "in": 0,
                    "out": target_duration,
                    "loop_music": True,
                    "volume": 20,
                }
                project = {**current, "audio_tracks": [*audio_tracks(current), target_track]}
                validate_clip(updated)
            except (KeyError, ValueError) as error:
                return ui.notify(t(str(error)), type="warning")
            busy = True
            try:
                await persist({**project, "clips": [*project["clips"], updated]}, updated["id"])
            finally:
                busy = False

        async def context_duplicate(event):
            nonlocal busy
            if not current or busy:
                return
            clips = current["clips"]
            if "id" in event.args:
                clip = next((row for row in clips if row["id"] == event.args["id"]), None)
                if clip is None:
                    return
                updated, focus = duplicate_clip(clips, clip)
                candidate = {**current, "clips": updated}
            else:
                try:
                    candidate, focus = duplicate_track(current, event.args.get("track"))
                except ValueError as error:
                    return ui.notify(t(str(error)), type="warning")
            busy = True
            try:
                await persist(candidate, focus)
            finally:
                busy = False

        def open_audio_effects(identifier):
            if not current or busy:
                return
            clip = next((row for row in current["clips"] if row["id"] == identifier and row["has_audio"]), None)
            if not clip:
                return ui.notify(t("Selecione um clipe com áudio."), type="warning")
            with ui.dialog() as dialog, ui.card().classes("studio-card w-full max-w-md gap-4"):
                ui.label(t("Efeitos de áudio")).classes("text-lg font-semibold")
                ui.label(clip["name"]).classes("studio-muted")
                ui.label(t("Altura do som (semitons)"))
                pitch = (
                    ui.slider(
                        min=-PITCH_LIMIT,
                        max=PITCH_LIMIT,
                        value=clip.get("pitch", 0),
                        step=0.5,
                    )
                    .props("label-always")
                    .classes("w-full")
                )
                gain = {}
                for band, (label, _) in EQ_BANDS.items():
                    ui.label(f"{t(label)} (dB)")
                    gain[band] = (
                        ui.slider(
                            min=-EQ_LIMIT,
                            max=EQ_LIMIT,
                            value=clip.get("equalizer", {}).get(band, 0),
                            step=0.5,
                        )
                        .props("label-always")
                        .classes("w-full")
                    )

                async def apply():
                    if await replace_clip(
                        clip, pitch=pitch.value, equalizer={band: slider.value for band, slider in gain.items()}
                    ):
                        dialog.close()
                        await render(True, seek=clip["start"])

                def reset():
                    pitch.set_value(0)
                    for slider in gain.values():
                        slider.set_value(0)

                with ui.row():
                    ui.button(t("Aplicar e gerar prévia"), icon="check", on_click=apply)
                    ui.button(t("Restaurar padrão"), icon="undo", on_click=reset).props("flat")
            dialog.open()

        def open_video_effects(identifier):
            if not current or busy:
                return
            clip = next((row for row in current["clips"] if row["id"] == identifier and row["track"] == "V1"), None)
            if not clip:
                return ui.notify(t("Selecione um clipe do vídeo principal."), type="warning")
            values = clip.get("video_effects", {})
            legacy_pinch = values.get("pinch", {})
            if legacy_pinch.get("amount", 0) < 0 and "punch" not in values:
                values = {
                    **values,
                    "pinch": {**legacy_pinch, "enabled": False, "amount": 0},
                    "punch": {**legacy_pinch, "amount": -legacy_pinch["amount"]},
                }
            controls = {}
            enabled = {}
            colors = {}
            resetting = False

            def enable_when_adjusted(effect):
                if not resetting:
                    enabled[effect].set_value(True)

            with ui.dialog() as dialog, ui.card().classes("studio-card w-[760px] max-w-[95vw] gap-4"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.column().classes("gap-0"):
                        ui.label(t("Efeitos de vídeo")).classes("text-xl font-semibold")
                        ui.label(clip["name"]).classes("studio-muted")
                    ui.icon("auto_fix_high", size="28px", color="primary")
                with (
                    ui.element("div")
                    .classes("video-fx-scroll w-full min-w-0 overflow-x-auto")
                    .props(f'tabindex="0" role="region" aria-label="{t("Efeitos de vídeo")}"')
                ):
                    with ui.tabs().classes("w-max min-w-full").props("inline-label align=left") as effects_tabs:
                        tabs = {key: ui.tab(key, label=t(title)) for key, title in FX_TITLES.items()}
                with ui.tab_panels(effects_tabs, value=tabs["hsl"]).classes("w-full"):
                    for effect, parameters in FX_CONTROLS.items():
                        saved = values.get(effect, {})
                        with ui.tab_panel(tabs[effect]).classes("gap-4"):
                            enabled[effect] = ui.switch(t("Ativar efeito"), value=saved.get("enabled", False))
                            controls[effect] = {}
                            if effect == "gradient":
                                with ui.row().classes("w-full gap-4"):
                                    for key, label in (
                                        ("shadows", t("Cor das sombras")),
                                        ("highlights", t("Cor das luzes")),
                                    ):
                                        colors[key] = (
                                            ui.color_input(
                                                label,
                                                value=saved.get(key, GRADIENT_COLORS[key]),
                                                preview=True,
                                            )
                                            .props("outlined dense")
                                            .classes("flex-1")
                                        )
                                strip = ui.element("div").classes("w-full h-8 rounded-lg")

                                def update_gradient():
                                    pair = [control.value for control in colors.values()]
                                    if all(
                                        isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value)
                                        for value in pair
                                    ):
                                        strip.style(f"background:linear-gradient(90deg,{pair[0]},{pair[1]})")

                                def change_gradient():
                                    update_gradient()
                                    enable_when_adjusted("gradient")

                                for control in colors.values():
                                    control.on_value_change(change_gradient)
                                update_gradient()
                            for key, (label, minimum, maximum, default) in parameters.items():
                                with ui.row().classes("w-full items-center gap-4"):
                                    ui.label(t(label)).classes("w-44 text-sm")
                                    controls[effect][key] = (
                                        ui.slider(
                                            min=minimum,
                                            max=maximum,
                                            step=1,
                                            value=min(saved.get(key, default), maximum),
                                            on_change=lambda event, name=effect: enable_when_adjusted(name),
                                        )
                                        .props("label-always")
                                        .classes("flex-1 min-w-28")
                                    )
                ui.label(t("Mudar um controle ativa o efeito. A prévia abre no ponto do clipe.")).classes(
                    "studio-muted"
                )

                async def apply():
                    effects = {
                        effect: {
                            "enabled": enabled[effect].value,
                            **{key: slider.value for key, slider in group.items()},
                        }
                        for effect, group in controls.items()
                    }
                    effects["gradient"].update({key: control.value for key, control in colors.items()})
                    if await replace_clip(clip, video_effects=effects):
                        dialog.close()
                        length = clip["out"] - clip["in"]
                        position = (
                            playhead
                            if clip["start"] <= playhead < clip["start"] + length
                            else (clip["start"] + min(0.10, length / 2))
                        )
                        ui.run_javascript(f"getHtmlElement({preview_panel.id})?.scrollIntoView({{block: 'nearest'}})")
                        await render(True, seek=position)

                def reset():
                    nonlocal resetting
                    resetting = True
                    try:
                        for effect, group in controls.items():
                            for key, slider in group.items():
                                slider.set_value(FX_CONTROLS[effect][key][3])
                            enabled[effect].set_value(False)
                        for key, control in colors.items():
                            control.set_value(GRADIENT_COLORS[key])
                    finally:
                        resetting = False

                with ui.row():
                    ui.button(t("Aplicar e gerar prévia"), icon="check", on_click=apply)
                    ui.button(t("Restaurar padrão"), icon="undo", on_click=reset).props("flat")
                    ui.button(t("Fechar"), on_click=dialog.close).props("flat")
            dialog.open()

        def open_transitions(identifier):
            if not current or busy:
                return
            clip = next((row for row in current["clips"] if row["id"] == identifier), None)
            if not clip or (clip["kind"] != "video" and not clip["has_audio"]):
                return ui.notify(t("Selecione um clipe de vídeo ou áudio para ajustar a transição."), type="warning")
            has_video, has_audio = clip["kind"] == "video", clip["has_audio"]
            with ui.dialog() as dialog, ui.card().classes("studio-card w-full max-w-lg gap-4"):
                ui.label(t("Transições")).classes("text-xl font-semibold")
                ui.label(clip["name"]).classes("studio-muted")
                if has_video:
                    saved = clip.get("video_transition", {})
                    video_kind = (
                        ui.select(
                            {key: t(label) for key, label in VIDEO_TRANSITIONS.items()},
                            value=saved.get("kind", "none"),
                            label=t("Transição de vídeo"),
                        )
                        .props("outlined dense")
                        .classes("w-full")
                    )
                    length = ui.number(
                        t("Duração da transição (s)"),
                        value=saved.get("duration", 0.5),
                        min=0.05,
                        max=10,
                        step=0.05,
                    ).props("outlined dense")
                    overlap = ui.checkbox(t("Sobrepor ao clipe anterior na mesma faixa"))
                if has_audio:
                    saved = clip.get("audio_transition", {})
                    with ui.row().classes("w-full"):
                        fade_in = ui.number(
                            t("Entrada gradual (s)"),
                            value=saved.get("fade_in", 0),
                            min=0,
                            max=10,
                            step=0.05,
                        ).props("outlined dense")
                        fade_out = ui.number(
                            t("Saída gradual (s)"),
                            value=saved.get("fade_out", 0),
                            min=0,
                            max=10,
                            step=0.05,
                        ).props("outlined dense")
                    curve = ui.select(
                        {key: t(label) for key, label in AUDIO_CURVES.items()},
                        value=saved.get("curve", "tri"),
                        label=t("Curva do volume"),
                    ).props("outlined dense")
                    blend = ui.checkbox(t("Mesclar com o áudio anterior na mesma faixa"))
                    blend_duration = (
                        ui.number(
                            t("Duração da mescla (s)"),
                            value=0.5,
                            min=0.05,
                            max=10,
                            step=0.05,
                        )
                        .props("outlined dense")
                        .bind_visibility_from(blend, "value")
                    )

                async def apply():
                    values = {}
                    related = {}
                    try:
                        if has_video:
                            seconds = length.value or 0
                            values["video_transition"] = {"kind": video_kind.value, "duration": seconds}
                            if overlap.value and video_kind.value != "none":
                                _, start, actual = overlap_start(current["clips"], clip, seconds)
                                values["start"] = start
                                values["video_transition"]["duration"] = actual
                        if has_audio:
                            values["audio_transition"] = {
                                "fade_in": fade_in.value or 0,
                                "fade_out": fade_out.value or 0,
                                "curve": curve.value,
                            }
                            if blend.value:
                                previous, start, actual = overlap_start(
                                    current["clips"], clip, blend_duration.value or 0
                                )
                                values["start"] = start
                                values["audio_transition"]["fade_in"] = actual
                                related[previous["id"]] = {
                                    "audio_transition": {
                                        **previous.get("audio_transition", {}),
                                        "fade_out": actual,
                                        "curve": curve.value,
                                    }
                                }
                    except ValueError as error:
                        return ui.notify(t(str(error)), type="warning")
                    if await replace_clip(clip, related=related, **values):
                        dialog.close()
                        await render(True, seek=values.get("start", clip["start"]))

                ui.button(t("Aplicar e gerar prévia"), icon="check", on_click=apply)
            dialog.open()

        async def render(preview, *, seek=None):
            nonlocal busy
            if busy:
                return
            if not current or not current["clips"]:
                return ui.notify(t("Adicione um vídeo ou áudio à linha do tempo."), type="warning")
            busy = True
            status.set_text(t("Renderizando a prévia da montagem…" if preview else "Exportando a montagem…"))
            bar.set_visibility(True)
            try:
                output = await action(render_montage, service, current, preview=preview)
                if output:
                    set_result(output[0], None if preview else output[1], seek=seek)
                    status.set_text(t("Prévia da montagem pronta." if preview else "Montagem pronta para baixar."))
                else:
                    status.set_text("")
            finally:
                busy = False
                bar.set_visibility(False)

        def open_assistant():
            async def commit(original, candidate, selection, cursor):
                nonlocal busy
                if busy or current != original:
                    ui.notify(t("O projeto mudou. Gere um novo plano de edição."), type="warning")
                    return False
                busy = True
                try:
                    clip_ids = {row["id"] for row in candidate["clips"]}
                    saved = await persist(candidate, selection if selection in clip_ids else None)
                    if saved:
                        profile.set_value(saved["profile"])
                finally:
                    busy = False
                if saved and saved["clips"]:
                    await render(True, seek=min(cursor, sequence_duration(saved["clips"])))
                return bool(saved)

            if not current:
                return ui.notify(t("Abra um projeto de vídeo para usar o console."), type="warning")
            assistant_dialog(service, action, lambda: (current, selected, playhead), commit)

        @ui.refreshable
        def inspector():
            clip = next((row for row in current["clips"] if row["id"] == selected), None) if current else None
            ui.label(t("Editar clipe")).classes("text-base font-semibold")
            if not clip:
                ui.label(t("Selecione um clipe na linha do tempo.")).classes("studio-muted")
                return
            title = ui.input(t("Nome do clipe"), value=clip["name"]).props("outlined dense").classes("w-full")
            tracks = {
                "audio": audio_tracks(current),
                "video": video_tracks(current["clips"]),
                "caption": TRACKS["caption"],
                "text": TRACKS["text"],
            }
            track = ui.select(
                list(tracks[clip["kind"]]),
                value=clip["track"],
                label=t("Faixa"),
            ).props("outlined dense")
            with ui.row().classes("w-full"):
                start = ui.number(t("Posição na linha do tempo"), value=clip["start"], min=0, step=0.001).props(
                    "outlined dense"
                )
                first = ui.number(t("Início do corte (s)"), value=clip["in"], min=0, step=0.001).props("outlined dense")
                last = ui.number(t("Fim do corte (s)"), value=clip["out"], min=0, step=0.001).props("outlined dense")
            gain = ui.number(t("Volume (%)"), value=clip["volume"], min=0, max=200).props("outlined dense")
            gain.set_visibility(clip["has_audio"])
            pitch = ui.number(
                t("Altura do som (semitons)"),
                value=clip.get("pitch", 0),
                min=-PITCH_LIMIT,
                max=PITCH_LIMIT,
                step=0.5,
            ).props("outlined dense")
            pitch.set_visibility(clip["has_audio"])
            text_panel = font_size = None
            if clip["kind"] == "caption" and "text" in clip:
                text_panel = ui.textarea(t("Texto da legenda"), value=clip["text"]).props("outlined").classes("w-full")
            if clip["kind"] == "text":
                text_panel = ui.textarea(t("Texto na tela"), value=clip["text"]).props("outlined").classes("w-full")
                font_size = ui.number(t("Tamanho da fonte"), value=clip.get("font_size", 48), min=12, max=150).props(
                    "outlined dense"
                )

            async def apply():
                await replace_clip(
                    clip,
                    name=(title.value or "").strip() or clip["name"],
                    track=track.value,
                    start=start.value,
                    **{"in": first.value, "out": last.value},
                    volume=gain.value if gain.value is not None else 100,
                    pitch=pitch.value if pitch.value is not None else 0,
                    **({"text": (text_panel.value or "").strip()} if text_panel else {}),
                    **({"font_size": font_size.value or 48} if font_size else {}),
                )

            async def split():
                await split_clip(clip["id"])

            async def separate_audio():
                nonlocal busy
                if busy:
                    return
                busy = True
                try:
                    layers = separate_video_audio(clip)
                    updated = [
                        item for row in current["clips"] for item in (layers if row["id"] == clip["id"] else [row])
                    ]
                    await persist({**current, "clips": updated}, layers[-1]["id"])
                finally:
                    busy = False

            async def remove():
                nonlocal busy
                if busy:
                    return
                busy = True
                try:
                    await persist({**current, "clips": [row for row in current["clips"] if row["id"] != clip["id"]]})
                finally:
                    busy = False

            with ui.row().classes("w-full"):
                ui.button(t("Aplicar ao clipe"), icon="check", on_click=apply)
                ui.button(t("Dividir no cursor"), icon="content_cut", on_click=split).props("outline")
                ui.button(t("Remover clipe"), icon="delete_outline", on_click=remove).props("flat")
                if clip["kind"] == "video" and clip["has_audio"]:
                    ui.button(t("Separar áudio do vídeo"), icon="graphic_eq", on_click=separate_audio).props("flat")

        with shell("Editor de vídeo avançado"):
            ui.label(t("Editor de vídeo avançado")).classes("studio-title")
            with ui.card().classes("studio-card w-full"):
                with ui.row().classes("w-full items-center"):
                    projects = ui.select({}, label=t("Projetos de vídeo")).props("outlined dense").classes("min-w-48")
                    ui.button(t("Abrir projeto"), icon="folder_open", on_click=open_project).props("flat")
                    name = ui.input(t("Nome do projeto")).props("outlined dense")
                    profile = ui.select(
                        {
                            "480p": "480p · 16:9",
                            "720p": "HD · 16:9",
                            "1080p": "Full HD · 16:9",
                            "4k": "4K · 16:9",
                            "vertical": t("Vertical · 9:16"),
                            "square": "Quadrado · 1:1",
                            "portrait": "Retrato · 4:5",
                            "cinema": "Cinema · 21:9",
                        },
                        value="720p",
                        label=t("Formato da montagem"),
                    ).props("outlined dense")
                    ui.button(t("Criar projeto"), icon="add", on_click=new_project).props("outline")
                    ui.button(t("Salvar configurações"), icon="save", on_click=save_settings)
            with ui.element("div").classes("w-full grid grid-cols-1 lg:grid-cols-[240px_minmax(0,1fr)] gap-4"):
                with ui.card().classes("studio-card w-full"):
                    ui.label(t("Mídia do Studio")).classes("text-base font-semibold")
                    category = (
                        ui.select(
                            {
                                "": t("Todas as mídias"),
                                "video": t("Vídeo"),
                                "audio": t("Áudio"),
                                "caption": t("Legendas"),
                            },
                            value="",
                            label=t("Tipo de mídia"),
                        )
                        .props("outlined dense")
                        .classes("w-full")
                    )
                    assets = (
                        ui.select({}, label=t("Buscar na biblioteca"), with_input=True)
                        .props("outlined dense")
                        .classes("w-full")
                    )
                    category.on_value_change(library_options)
                    ui.button(t("Adicionar no cursor"), icon="add", on_click=add_selected)
                    ui.button(t("Adicionar música como fundo · 20%"), icon="music_note", on_click=add_background_music).props("outline")
                    ui.button(t("Prévia do arquivo"), icon="play_circle", on_click=inspect_asset).props("flat")
                    ui.button(t("Adicionar legenda"), icon="subtitles", on_click=add_caption).props("outline")
                    ui.button(t("Adicionar texto"), icon="title", on_click=add_title).props("outline")
                    upload_box = (
                        ui.upload(
                            label=t("Importar mídia"),
                            auto_upload=True,
                            max_file_size=VIDEO_LIMIT,
                            on_upload=upload,
                        )
                        .props(f'accept="{",".join((*VIDEO_EXTENSIONS, *MUSIC_EXTENSIONS, ".srt"))}"')
                        .classes("w-full")
                    )
                    ui.button(t("Atualizar biblioteca"), icon="refresh", on_click=refresh_library).props("flat")
                with ui.card().classes("studio-card w-full") as preview_panel:
                    ui.label(t("Prévia da montagem")).classes("text-base font-semibold")
                    player = ui.video("").classes("w-full aspect-video bg-slate-950 rounded-lg")
                    player.on("loadedmetadata", seek_new_preview)
                    empty_player = ui.label(t("Gere uma prévia para assistir à montagem.")).classes(
                        "flex items-center justify-center w-full aspect-video bg-slate-900 text-white rounded-lg"
                    )
                    player.set_visibility(False)
                    with ui.row():
                        ui.button(t("Gerar prévia"), icon="play_circle", on_click=lambda: render(True)).props("outline")
                        ui.button(t("Exportar vídeo"), icon="movie", on_click=lambda: render(False))
                    status = ui.label("").classes("studio-muted")
                    bar = ui.linear_progress(value=0).props("indeterminate").classes("w-full")
                    bar.set_visibility(False)
                    result = ui.row().classes("w-full")
            with ui.row().classes("w-full"):
                ui.button(t("Adicionar faixa de áudio"), icon="playlist_add", on_click=add_audio_track).props("outline")
                ui.button(t("Console de IA"), icon="auto_awesome", on_click=open_assistant).props("outline")
                ui.button(t("Transições"), icon="animation", on_click=lambda: open_transitions(selected)).props(
                    "outline"
                )
                ui.button(
                    t("Efeitos de áudio"),
                    icon="graphic_eq",
                    on_click=lambda: open_audio_effects(selected),
                ).props("outline")
                ui.button(
                    t("Efeitos de vídeo"),
                    icon="auto_fix_high",
                    on_click=lambda: open_video_effects(selected),
                ).props("outline")
            timeline = MontageTimeline(
                t,
                player,
                set_active,
                move_clip,
                position,
                key_split,
                context_duplicate,
                lambda event: open_audio_effects(event.args),
                lambda event: open_video_effects(event.args),
                lambda event: open_transitions(event.args),
            ).classes("w-full")
            with ui.card().classes("studio-card w-full"):
                inspector()

        if project_id:
            try:
                current = store.open(project_id)
            except ValueError as error:
                ui.notify(t(str(error)), type="warning")
        reload_projects()
        show_project()
        ui.timer(0.1, refresh_library, once=True)
