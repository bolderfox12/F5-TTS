"""Review and apply proposed timeline edits in the montage editor."""

from nicegui import ui

from f5_tts.studio.microphone import Microphone
from f5_tts.studio.montage_assistant import plan_edits
from f5_tts.studio.script_review import local_review_models, preferred_review_model


EXAMPLES = (
    ("Cortar", "Divida o clipe selecionado na posição atual do cursor."),
    ("Mixar", "Abaixe em 6 dB o volume do clipe de áudio selecionado."),
    ("Transição", "Coloque uma dissolução de 0,5 s com o vídeo anterior na mesma faixa."),
    ("Legenda", "Adicione a legenda de 3 segundos na posição do cursor com o texto: "),
    ("Tela final", "Adicione o texto 'Inscreva-se no canal' nos últimos 5 segundos."),
    ("YouTube", "Configure a exportação em 1080p horizontal."),
    ("4K", "Configure a exportação em 4K horizontal."),
    ("Quadrado", "Configure a exportação quadrada 1:1."),
    ("Shorts", "Configure a exportação em formato vertical para Shorts."),
    ("Publicação", "Sugira título, descrição, texto de capa e capítulos. Tema e tempos: "),
)


def assistant_dialog(service, action, snapshot, commit):
    t = service.translate_ui
    working = False

    async def update_models():
        if provider.value == "gemini":
            local.set_visibility(False)
            model.set_visibility(True)
            return
        model.set_visibility(False)
        local.set_visibility(True)
        models = await action(local_review_models)
        if provider.value == "ollama" and models is not None:
            preferred = preferred_review_model("ollama")
            qwen = next((item for item in models if "qwen" in item.casefold()), None)
            local.set_options(models, value=preferred if preferred in models else qwen or (models or [None])[0])

    async def receive_voice(event):
        nonlocal working
        if working:
            recorder.run_method("finish", False)
            return
        working = True
        try:
            transcript = await action(service.transcribe_chat_recording, event.args)
            if transcript:
                instruction.set_value(transcript)
            recorder.run_method("finish", bool(transcript))
        finally:
            working = False

    async def generate():
        nonlocal working
        if working:
            return
        project, selected, playhead = snapshot()
        if not project:
            return ui.notify(t("Abra um projeto de vídeo para usar o console."), type="warning")
        text = (instruction.value or "").strip()
        if not text or len(text) > 4000:
            return ui.notify(t("Informe um comando de até 4.000 caracteres."), type="warning")
        chosen = local.value if provider.value == "ollama" else model.value
        if not chosen:
            return ui.notify(t("Selecione um modelo de IA."), type="warning")
        working = True
        output.clear()
        ask.disable()
        try:
            result = await action(plan_edits, service, project, selected, playhead, text, provider.value, chosen)
        finally:
            ask.enable()
            working = False
        if result is None:
            return
        candidate, review, note, publication = result

        async def apply():
            if await commit(project, candidate, selected, playhead):
                dialog.close()

        with output:
            if note:
                ui.label(note).classes("whitespace-pre-wrap")
            for line in review:
                ui.label(line).classes("whitespace-pre-wrap")
            if publication:
                ui.textarea(t("Texto para publicação no YouTube"), value=publication).props("outlined").classes(
                    "w-full"
                )
            if review:
                ui.button(t("Aplicar e gerar prévia"), icon="check", on_click=apply)
            elif not note and not publication:
                ui.label(t("Nenhuma alteração foi sugerida."))

    with ui.dialog() as dialog, ui.card().classes("studio-card w-full max-w-3xl"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(t("Console de IA")).classes("font-semibold text-lg")
            ui.button(icon="close", on_click=dialog.close).props("flat round").tooltip(t("Fechar"))
        ui.label(
            t("Use os nomes dos clipes, a seleção ou os tempos da timeline. Revise o plano antes de aplicar.")
        ).classes("studio-muted")
        with ui.row().classes("w-full"):
            provider = ui.select(
                {"gemini": "Gemini", "ollama": t("Ollama local")},
                value="gemini",
                label=t("Provedor"),
                on_change=update_models,
            ).props("outlined")
            model = ui.input(t("Modelo Gemini"), value=preferred_review_model("gemini")).props("outlined")
            local = ui.select([], label=t("Modelo no Ollama")).props("outlined")
            local.set_visibility(False)
        instruction = ui.textarea(t("O que você quer alterar?")).props("outlined maxlength=4000").classes("w-full")
        with ui.row().classes("w-full gap-1"):
            for label, example in EXAMPLES:
                ui.button(t(label), on_click=lambda _, value=example: instruction.set_value(t(value))).props(
                    "outline no-caps dense"
                )
        recorder = Microphone(receive_voice, t, for_command=True).classes("w-full")
        ask = ui.button(t("Sugerir alterações"), icon="auto_awesome", on_click=generate)
        output = ui.column().classes("w-full")
    dialog.open()
