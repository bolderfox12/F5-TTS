"""Editable speech cards and status for the dubbing review."""

from nicegui import ui

from f5_tts.studio.local_tools import srt_time


def speech_card(
    t,
    row,
    speakers,
    translated,
    preview,
    *,
    change,
    seek,
    regenerate,
    busy,
    takes_panel=None,
    shorten=None,
    suggestions=(),
    apply=None,
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
            ui.button(
                srt_time(row["start"] or 0).replace(",", "."),
                icon="play_arrow",
                on_click=seek,
            ).props("flat no-caps").tooltip(t("Ir para esta fala"))
            for key, title in (("start", t("Início (s)")), ("end", t("Fim (s)"))):

                def timing(event, field=key):
                    row[field] = event.value
                    update_badge()

                ui.number(title, value=row[key], min=0, step=0.001, on_change=timing).props("outlined dense").classes(
                    "w-28"
                ).set_enabled(not busy)
            ui.select(
                speakers,
                value=row["speaker"],
                label=t("Personagem"),
                on_change=lambda event: change("speaker", event.value),
            ).props("outlined dense").classes("w-44").set_enabled(not busy)

        def replace_translation(event):
            change("translated", event.value)
            if preview:
                mini_player.set_visibility(event.value == translated)
                badge.set_visibility(event.value == translated)

        with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-2 gap-3"):
            ui.textarea(
                t("Transcrição original"), value=row["text"], on_change=lambda event: change("text", event.value)
            ).props("outlined dense autogrow").set_enabled(not busy)
            ui.textarea(t("Texto traduzido"), value=translated, on_change=replace_translation).props(
                "outlined dense autogrow"
            ).set_enabled(not busy)
        if takes_panel:
            takes_panel()
        else:
            with ui.row().classes("w-full items-center"):
                if preview:
                    mini_player = ui.audio(preview[0]).classes("w-full max-w-sm")
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
