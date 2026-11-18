"""Inline reference assignments for a script in Create audio."""

from collections import defaultdict, deque

from nicegui import ui

from f5_tts.studio.story_tools import split_reference_turns


def reference_turn_editor(t, script, voices, audio_upload, main_reference):
    enabled = ui.switch(t("Escolher uma referência por trecho"))
    state = {"script": None, "rows": []}
    with ui.column().classes("w-full gap-3") as editor:
        ui.label(
            t("Separe os trechos com linhas em branco ou escreva uma fala por linha. Exemplo: Ana: Vamos começar.")
        ).classes("text-sm studio-muted")
        ui.label(t("Para mudar a emoção, escolha outra gravação da mesma pessoa com a entonação desejada.")).classes(
            "text-sm studio-muted"
        )
        ui.link(t("Abrir Minhas vozes"), "/voices", new_tab=True)

        options = {
            "main": t("Referência principal"),
            "upload": t("Enviar áudio para este trecho"),
            **voices,
        }

        @ui.refreshable
        def display_parts():
            for index, part in enumerate(state["rows"], 1):
                title = f"{index}. {part['speaker']}" if part["speaker"] else t("Trecho {number}", number=index)
                with ui.expansion(title, caption=part["text"][:100], value=index == 1).classes("w-full"):
                    ui.label(part["text"]).classes("text-sm whitespace-pre-wrap")
                    select = (
                        ui.select(
                            options,
                            label=t("Referência do trecho"),
                            value=part["selection"],
                            on_change=lambda event, item=part: item.update(selection=event.value),
                        )
                        .props("outlined")
                        .classes("w-full")
                    )
                    with ui.column().classes("w-full") as upload:
                        upload_status = ui.label(t("Nenhum arquivo enviado.")).classes("text-sm")
                        preview = ui.audio(part["paths"][-1] if part["paths"] else "").classes("w-full")
                        if part["paths"]:
                            upload_status.set_text(t("Referência pronta para usar."))

                        async def update_preview(item=part, player=preview):
                            player.set_source(item["paths"][-1])

                        audio_upload(part["paths"], upload_status, reference=True, on_reference=update_preview)
                        ui.textarea(
                            t("O que é dito na referência? (opcional)"),
                            value=part["transcript"],
                            on_change=lambda event, item=part: item.update(transcript=event.value),
                        ).props("outlined rows=2").classes("w-full")
                    upload.bind_visibility_from(select, "value", backward=lambda value: value == "upload")
                    if part["speaker"]:
                        ui.button(
                            t("Usar esta referência nas falas do mesmo personagem"),
                            on_click=lambda item=part: repeat_reference(item),
                        ).props("flat no-caps")

        def repeat_reference(part):
            for item in state["rows"]:
                if item is not part and item["speaker"].casefold() == part["speaker"].casefold():
                    item.update(selection=part["selection"], paths=list(part["paths"]), transcript=part["transcript"])
            display_parts.refresh()

        def prepare():
            original = script.value or ""
            try:
                result = split_reference_turns(original)
            except ValueError as error:
                ui.notify(t(str(error)), type="warning")
                return
            previous = defaultdict(deque)
            for part in state["rows"]:
                previous[(part["speaker"], part["text"])].append(part)
            state.update(
                script=original,
                rows=[
                    previous[(part["speaker"], part["text"])].popleft()
                    if previous[(part["speaker"], part["text"])]
                    else {**part, "selection": "main", "paths": [], "transcript": ""}
                    for part in result
                ],
            )
            display_parts.refresh()

        ui.button(t("Separar trechos do roteiro"), icon="segment", on_click=prepare).props("outline no-caps")
        display_parts()
    editor.bind_visibility_from(enabled, "value")

    def selected_parts():
        if state["script"] != (script.value or ""):
            raise ValueError("Clique em Separar trechos do roteiro para atualizar as referências.")
        parts = []
        for row in state["rows"]:
            selection = row["selection"]
            if selection == "main":
                reference = main_reference()
            elif selection == "upload":
                reference = {
                    "reference": row["paths"][-1] if row["paths"] else None,
                    "transcript": row["transcript"],
                }
            else:
                reference = {"voice_id": selection}
            if not reference.get("reference") and not reference.get("voice_id"):
                raise ValueError("Escolha uma referência para cada trecho.")
            parts.append({"speaker": row["speaker"], "text": row["text"], **reference})
        return parts

    return enabled, selected_parts
