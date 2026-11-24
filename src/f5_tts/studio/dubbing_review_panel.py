"""Editable speech cards and status for the dubbing review."""

from nicegui import ui

from f5_tts.studio.local_tools import srt_time
from f5_tts.studio.dubbing_transcript import needs_translation_review
from f5_tts.studio.vocal_profiles import PROFILE_ICONS, PROFILE_LABELS, profile_check_status


def speech_card(
    t,
    row,
    speakers,
    translated,
    preview,
    *,
    number,
    change,
    seek,
    regenerate,
    busy,
    takes_panel=None,
    shorten=None,
    suggestions=(),
    apply=None,
    gemini_style=None,
    synthesis_state=None,
    transcript_issue="",
    remove=None,
    include=True,
):
    with ui.card().classes("studio-card w-full p-4 gap-3") as card:
        badge = ui.badge().props("outline")
        if row.get("recovered"):
            ui.badge(t("Fala recuperada"), color="teal-8")

        def update_badge():
            start, end = row["start"], row["end"]
            duration = preview[1] if preview else None
            overflow = duration - (end - start) if duration and start is not None and end is not None else 0
            card.classes(
                add="ring-2 ring-amber-400" if overflow > 0.001 else "",
                remove="ring-2 ring-amber-400" if overflow <= 0.001 else "",
            )
            badge.set_visibility(duration is not None)
            if overflow > 0.001:
                badge.set_text(t("Excede {value_0} s", value_0=f"{overflow:.2f}"))
                badge.props("color=deep-orange-9")
            elif duration is not None:
                badge.set_text(t("Áudio de {value_0} s", value_0=f"{duration:.3f}"))
                badge.props("color=teal-9")

        update_badge()
        card.on(
            "click",
            seek,
            js_handler="(event, emit) => { if (!event.target.closest('button,input,textarea,label,audio,[role=combobox]')) emit(); }",
        )
        with ui.row().classes("w-full items-center"):
            ui.checkbox(
                t("Incluir na dublagem"),
                value=include,
                on_change=lambda event: change("include_in_dub", event.value),
            ).set_enabled(not busy)
            profile = row.get("vocal_profile", "unknown")
            ui.icon(PROFILE_ICONS[profile], size="28px").tooltip(t(PROFILE_LABELS[profile]))
            ui.label(t("Fala {value_0} · Personagem {value_1}", value_0=number, value_1=row["speaker"])).classes(
                "font-semibold"
            )
            if synthesis_state is not None:
                synthesis_badge = ui.badge()
            if transcript_issue:
                issue_labels = {
                    "repeat": "Revisar repetição na transcrição",
                    "duplicate": "Fala duplicada no mesmo trecho",
                    "nearby": "Revisar fala repetida no trecho vizinho",
                }
                ui.badge(t(issue_labels[transcript_issue]), color="deep-orange-9")
            checked = profile_check_status(row)
            if checked == "mismatch":
                detected = t(PROFILE_LABELS[row["vocal_check"]["profile"]])
                ui.badge(t("Revisar perfil. Áudio sugere {value_0}.", value_0=detected), color="deep-orange-9")
            elif checked == "unclear":
                ui.badge(t("Checagem inconclusiva"), color="amber-9", text_color="black")
            elif checked == "match":
                ui.badge(t("Perfil compatível"), color="teal-8")
            ui.button(
                srt_time(row["start"] or 0).replace(",", "."),
                icon="play_arrow",
                on_click=seek,
            ).props("flat no-caps").tooltip(t("Ir para esta fala"))
            for key, title in (("start", t("Início (s)")), ("end", t("Fim (s)"))):

                def timing(event, field=key):
                    row[field] = event.value
                    update_badge()
                    change(field, event.value)

                ui.number(title, value=row[key], min=0, step=0.001, on_change=timing).props("outlined dense").classes(
                    "w-28"
                ).set_enabled(not busy)
            ui.select(
                speakers,
                value=row["speaker"],
                label=t("Personagem"),
                on_change=lambda event: change("speaker", event.value),
            ).props("outlined dense").classes("w-44").set_enabled(not busy)
            ui.select(
                {key: t(label) for key, label in PROFILE_LABELS.items()},
                value=profile,
                label=t("Perfil vocal"),
                on_change=lambda event: change("vocal_profile", event.value),
            ).props("outlined dense").classes("w-36").set_enabled(not busy)
        if row.get("reference"):
            ui.label(t("Fala original")).classes("text-sm")
            ui.audio(str(row["reference"])).props("preload=none").classes("w-full max-w-sm")

        def replace_translation(event):
            change("translated", event.value)
            if synthesis_state is not None:
                confirm_repeat.set_visibility(needs_translation_review(row, event.value or "", transcript_issue))
            if preview and synthesis_state is None:
                mini_player.set_visibility(event.value == translated)
                badge.set_visibility(event.value == translated)

        with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-2 gap-3"):
            ui.textarea(
                t("Transcrição original"), value=row["text"], on_change=lambda event: change("text", event.value)
            ).props("outlined dense autogrow").set_enabled(not busy)
            ui.textarea(t("Texto traduzido"), value=translated, on_change=replace_translation).props(
                "outlined dense autogrow"
            ).set_enabled(not busy)
        if synthesis_state is not None:
            confirm_repeat = (
                ui.button(
                    t("Confirmar repetição intencional"),
                    on_click=lambda: change("confirm_repetition", True),
                )
                .props("flat no-caps")
                .set_enabled(not busy)
            )
            confirm_repeat.set_visibility(needs_translation_review(row, translated, transcript_issue))
        if remove:
            ui.button(t("Excluir fala suspeita"), icon="delete_outline", on_click=remove).props(
                "flat no-caps"
            ).set_enabled(not busy)
        if gemini_style is not None:

            def replace_style(event):
                change("gemini_style", event.value)
                if preview and synthesis_state is None:
                    mini_player.set_visibility(event.value == gemini_style)
                    badge.set_visibility(event.value == gemini_style)

            ui.input(t("Entonação da fala"), value=gemini_style, on_change=replace_style).props(
                "outlined dense maxlength=500"
            ).classes("w-full").set_enabled(not busy).tooltip(
                t("Exemplo: voz baixa, tensa e com uma pausa antes do final.")
            )
        if takes_panel:
            takes_panel()
        else:
            with ui.row().classes("w-full items-center"):
                if preview or synthesis_state is not None:
                    mini_player = (
                        ui.audio(preview[0] if preview else "").props("preload=none").classes("w-full max-w-sm")
                    )
                ui.button(t("Regerar fala"), icon="refresh", on_click=regenerate).props("outline no-caps").set_enabled(
                    not busy
                )

            if shorten:
                ui.button(t("Encurtar fala com Gemini"), icon="translate", on_click=shorten).props(
                    "flat no-caps"
                ).tooltip(t("Envia o texto desta fala e das vizinhas ao Gemini.")).set_enabled(not busy)
            if suggestions:
                with ui.column().classes("w-full gap-2"):
                    ui.label(t("Sugestões mais curtas")).classes("font-medium")
                    for alternative in suggestions:
                        with ui.row().classes("w-full items-center"):
                            ui.label(alternative).classes("flex-1")

                            def use(text=alternative):
                                apply(text)

                            ui.button(t("Usar esta fala"), icon="check", on_click=use).props(
                                "outline no-caps"
                            ).set_enabled(not busy)
                    ui.label(t("Depois de escolher, regenere a fala para conferir a duração.")).classes("studio-muted")

        if synthesis_state is not None:

            def update_synthesis(clip, state):
                nonlocal preview
                if clip != preview:
                    preview = clip
                    mini_player.set_source(clip[0] if clip else "")
                mini_player.set_visibility(clip is not None)
                synthesis_badge.set_visibility(bool(state))
                synthesis_badge.set_text(t(state))
                synthesis_badge.props("color=positive" if state == "OK" else "color=negative")
                update_badge()

            update_synthesis(preview, synthesis_state)
            return update_synthesis
