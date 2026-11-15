"""Persistent local model library, separate from voice references."""

from nicegui import ui


def register_model_pages(service, shell, action):
    t = service.translate_ui

    @ui.page("/models")
    def models_page():
        with shell("Meus modelos"):
            ui.label(t("Seus modelos, sem substituir o original.")).classes("studio-title")
            ui.label(
                t(
                    "Cadastre seu modelo em português e escolha entre ele e o original na hora de gerar áudio. Uma voz salva é uma referência; o modelo define o que foi aprendido no treinamento."
                )
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full gap-4"):
                name = ui.input(t("Nome do modelo"), placeholder=t("Ex.: Meu modelo em português")).props("outlined")
                architecture = ui.select(
                    {
                        "F5TTS_v1_Base": t("F5-TTS v1 Base"),
                        "F5TTS_Base": t("F5-TTS Base (anterior)"),
                        "E2TTS_Base": t("E2-TTS Base"),
                    },
                    value="F5TTS_v1_Base",
                    label=t("Arquitetura usada no treinamento"),
                ).props("outlined")
                checkpoint = ui.input(t("Caminho do checkpoint"), placeholder=t("C:\\Modelos\\portugues.pt")).props(
                    "outlined"
                )
                vocabulary = ui.input(t("Caminho do vocab.txt (opcional)")).props("outlined")
                ui.label(
                    t(
                        "Cole os caminhos dos arquivos ou escolha-os abaixo. Use o vocab.txt do treinamento; deixe vazio somente se o treinamento manteve o vocabulário original."
                    )
                ).classes("studio-muted text-sm")

                async def receive(event, target):
                    try:
                        path = service.model_upload_path(event.file.name)
                        await event.file.save(path)
                        target.set_value(str(path))
                        ui.notify(t("Arquivo recebido. Clique em Cadastrar modelo para concluir."), type="positive")
                    except Exception as error:
                        ui.notify(t(str(error)), type="negative")

                ui.upload(
                    label=t("Escolher arquivo do modelo"),
                    auto_upload=True,
                    max_files=1,
                    max_file_size=5_000_000_000,
                    on_upload=lambda event: receive(event, checkpoint),
                ).props('accept=".pt,.pth,.ckpt,.safetensors"').classes("w-full")
                ui.upload(
                    label=t("Escolher vocab.txt"),
                    auto_upload=True,
                    max_files=1,
                    max_file_size=5_000_000,
                    on_upload=lambda event: receive(event, vocabulary),
                ).props('accept=".txt"').classes("w-full")

                async def save():
                    result = await action(
                        service.register_model, name.value, checkpoint.value, vocabulary.value, architecture.value
                    )
                    if result:
                        cards.refresh()
                        ui.notify(
                            t("Modelo cadastrado. Selecione-o em Criar áudio ou Regravar áudio."), type="positive"
                        )

                ui.button(t("Cadastrar modelo"), icon="add", on_click=save).props("no-caps")

            @ui.refreshable
            def cards():
                for model in service.models():
                    with ui.card().classes("studio-card w-full"):
                        ui.label(t(model["name"]) if model["id"] == "original" else model["name"]).classes(
                            "font-semibold"
                        )
                        ui.label(model["architecture"]).classes("text-sm studio-muted")
                        if model["id"] == "original":
                            ui.label(t("Sempre disponível. Seus modelos cadastrados não substituem este modelo."))
                        else:
                            ui.label(model["checkpoint"]).classes("text-sm break-all")
                            ui.label(t("Vocabulário: ") + (model["vocabulary"] or "original")).classes(
                                "text-sm break-all"
                            )
                ui.button(t("Ir para Criar áudio"), icon="graphic_eq", on_click=lambda: ui.navigate.to("/")).props(
                    "no-caps"
                )

            cards()
