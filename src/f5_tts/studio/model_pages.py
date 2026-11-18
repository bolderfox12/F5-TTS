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
            ui.label(
                t(
                    "O inventário mostra o espaço ocupado. Arquivos enviados ao Studio podem ser apagados daqui; modelos ainda usados em histórias ou narrações ficam protegidos."
                )
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full gap-4"):
                name = ui.input(t("Nome do modelo"), placeholder=t("Ex.: Meu modelo em português")).props("outlined")
                architecture = ui.select(
                    {
                        "F5TTS_v1_Base": t("F5-TTS v1 Base"),
                        "F5TTS_Base": t("F5-TTS Base (anterior)"),
                        "F5TTS_Base_bigvgan": "F5-TTS Base · BigVGAN",
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
                        source.set_options(service.model_choices())
                        ui.notify(
                            t("Modelo cadastrado. Selecione-o em Criar áudio ou Regravar áudio."), type="positive"
                        )

                ui.button(t("Cadastrar modelo"), icon="add", on_click=save).props("no-caps")

            with ui.card().classes("studio-card w-full gap-4"):
                ui.label(t("Exportar checkpoint INT8 para CPU")).classes("font-semibold text-lg")
                source = ui.select(service.model_choices(), label=t("Modelo de origem")).props("outlined")
                int8_name = ui.input(t("Nome da cópia INT8")).props("outlined")
                result_label = ui.label("").classes("studio-muted")

                async def quantize():
                    int8_button.disable()
                    result_label.set_text(t("Quantizando pesos…"))
                    try:
                        result = await action(service.quantize_model, source.value, int8_name.value)
                        if result:
                            result_label.set_text(
                                t(
                                    "Modelo cadastrado · {before} → {after}",
                                    before=format_bytes(result["source_size"]),
                                    after=format_bytes(result["size"]),
                                )
                            )
                            cards.refresh()
                        else:
                            result_label.set_text("")
                    finally:
                        int8_button.enable()

                int8_button = ui.button(t("Criar cópia INT8"), icon="compress", on_click=quantize).props("no-caps")

            @ui.refreshable
            def cards():
                models = service.model_inventory()
                total = sum(model["size"] for model in models)
                ui.label(
                    t(
                        "{value_0} modelo(s) cadastrado(s) · {value_1} no disco",
                        value_0=len(models),
                        value_1=format_bytes(total),
                    )
                ).classes("font-semibold")
                if not models:
                    ui.label(t("Nenhum modelo personalizado cadastrado.")).classes("studio-muted")
                for model in models:
                    with ui.card().classes("studio-card w-full"):
                        with ui.row().classes("w-full items-center"):
                            ui.label(model["name"]).classes("font-semibold text-lg")
                            ui.space()
                            ui.badge(format_bytes(model["size"]), color="teal-1", text_color="teal-9")
                        ui.label(model["architecture"]).classes("text-sm studio-muted")
                        if model.get("quantized"):
                            ui.badge("INT8 · CPU")
                            with ui.row():
                                ui.button(
                                    t("Baixar pesos INT8"),
                                    on_click=lambda item=model: ui.download.file(
                                        item["checkpoint"], filename="modelo-int8.safetensors"
                                    ),
                                ).props("flat no-caps")
                                if model["vocabulary"]:
                                    ui.button(
                                        t("Baixar vocabulário"),
                                        on_click=lambda item=model: ui.download.file(
                                            item["vocabulary"], filename="vocab.txt"
                                        ),
                                    ).props("flat no-caps")
                        if model.get("orphan"):
                            ui.label(t("Arquivo de checkpoint enviado, ainda não cadastrado como modelo.")).classes(
                                "text-sm studio-muted"
                            )
                        ui.label(model["checkpoint"]).classes("text-sm break-all")
                        ui.label(t("Vocabulário: ") + (model["vocabulary"] or t("original"))).classes(
                            "text-sm break-all"
                        )
                        if not model["exists"]:
                            ui.badge(t("Arquivo não encontrado"), color="red-1", text_color="red-9")
                        if model["in_use"]:
                            message = (
                                t("Este checkpoint está selecionado como entrada de um treino.")
                                if model.get("training_input")
                                else t(
                                    "Em uso por uma história ou projeto. Troque o modelo nesses trabalhos antes de remover."
                                )
                            )
                            ui.label(message).classes("text-sm text-orange-9")
                        with ui.dialog() as confirm, ui.card().classes("studio-card w-full max-w-xl"):
                            ui.label(t("Remover modelo?")).classes("text-lg font-semibold")
                            ui.label(
                                t(
                                    "O cadastro será removido. A exclusão dos arquivos gerenciados pelo Studio é opcional e libera espaço no disco."
                                )
                            ).classes("studio-muted")
                            delete_files = ui.checkbox(
                                t("Apagar também o checkpoint local"),
                                value=model["managed"],
                            )
                            delete_files.set_enabled(model["managed"])
                            if model.get("project_checkpoint"):
                                ui.label(
                                    t(
                                        "A exclusão apaga esta versão da pasta ckpts do projeto; os dados de treino e o vocabulário são preservados."
                                    )
                                ).classes("text-sm text-orange-9")
                            ui.label(t("Arquivos externos e vocabulários compartilhados são preservados.")).classes(
                                "text-sm studio-muted"
                            )

                            async def remove_model(item=model, checkbox=delete_files, dialog=confirm):
                                freed = await action(service.delete_model, item["id"], checkbox.value)
                                if freed is not None:
                                    dialog.close()
                                    cards.refresh()
                                    if freed:
                                        ui.notify(
                                            t("Modelo removido. Espaço liberado: ") + format_bytes(freed),
                                            type="positive",
                                        )
                                    else:
                                        ui.notify(
                                            t("Cadastro removido; os arquivos foram preservados."), type="positive"
                                        )

                            with ui.row().classes("w-full justify-end"):
                                ui.button(t("Cancelar"), on_click=confirm.close).props("flat no-caps")
                                ui.button(t("Remover modelo"), icon="delete", on_click=remove_model).props(
                                    "color=negative no-caps"
                                )
                        with ui.row().classes("w-full justify-end"):
                            remove_button = ui.button(t("Remover"), icon="delete", on_click=confirm.open).props(
                                "flat no-caps"
                            )
                            remove_button.set_enabled(not model["in_use"])
                ui.button(t("Ir para Criar áudio"), icon="graphic_eq", on_click=lambda: ui.navigate.to("/")).props(
                    "no-caps"
                )

            cards()


def format_bytes(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
