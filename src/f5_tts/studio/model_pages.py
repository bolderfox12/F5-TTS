"""Persistent local model library, separate from voice references."""
from nicegui import ui


def register_model_pages(service, shell, action):
    @ui.page("/models")
    def models_page():
        with shell("Meus modelos"):
            ui.label("Seus modelos, sem substituir o original.").classes("studio-title")
            ui.label(
                "Cadastre seu modelo em português e escolha entre ele e o original na hora de gerar áudio. "
                "Uma voz salva é uma referência; o modelo define o que foi aprendido no treinamento."
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full gap-4"):
                name = ui.input("Nome do modelo", placeholder="Ex.: Meu modelo em português").props("outlined")
                architecture = ui.select(
                    {"F5TTS_v1_Base": "F5-TTS v1 Base", "F5TTS_Base": "F5-TTS Base (anterior)",
                     "E2TTS_Base": "E2-TTS Base"},
                    value="F5TTS_v1_Base", label="Arquitetura usada no treinamento",
                ).props("outlined")
                checkpoint = ui.input("Caminho do checkpoint", placeholder="C:\\Modelos\\portugues.pt").props("outlined")
                vocabulary = ui.input("Caminho do vocab.txt (opcional)").props("outlined")
                ui.label(
                    "Cole os caminhos dos arquivos ou escolha-os abaixo. Use o vocab.txt do treinamento; "
                    "deixe vazio somente se o treinamento manteve o vocabulário original."
                ).classes("studio-muted text-sm")

                async def receive(event, target):
                    try:
                        path = service.model_upload_path(event.file.name)
                        await event.file.save(path)
                        target.set_value(str(path))
                        ui.notify("Arquivo recebido. Clique em Cadastrar modelo para concluir.", type="positive")
                    except Exception as error:
                        ui.notify(str(error), type="negative")

                ui.upload(
                    label="Escolher arquivo do modelo", auto_upload=True, max_files=1,
                    max_file_size=5_000_000_000, on_upload=lambda event: receive(event, checkpoint),
                ).props('accept=".pt,.pth,.ckpt,.safetensors"').classes("w-full")
                ui.upload(
                    label="Escolher vocab.txt", auto_upload=True, max_files=1,
                    max_file_size=5_000_000, on_upload=lambda event: receive(event, vocabulary),
                ).props('accept=".txt"').classes("w-full")

                async def save():
                    result = await action(service.register_model, name.value, checkpoint.value,
                                          vocabulary.value, architecture.value)
                    if result:
                        cards.refresh()
                        ui.notify("Modelo cadastrado. Selecione-o em Criar áudio ou Regravar áudio.", type="positive")

                ui.button("Cadastrar modelo", icon="add", on_click=save).props("no-caps")

            @ui.refreshable
            def cards():
                for model in service.models():
                    with ui.card().classes("studio-card w-full"):
                        ui.label(model["name"]).classes("font-semibold")
                        ui.label(model["architecture"]).classes("text-sm studio-muted")
                        if model["id"] == "original":
                            ui.label("Sempre disponível. Seus modelos cadastrados não substituem este modelo.")
                        else:
                            ui.label(model["checkpoint"]).classes("text-sm break-all")
                            ui.label("Vocabulário: " + (model["vocabulary"] or "original")).classes("text-sm break-all")
                ui.button("Ir para Criar áudio", icon="graphic_eq", on_click=lambda: ui.navigate.to("/")).props("no-caps")

            cards()
