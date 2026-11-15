"""Local file library for generated audio and voice references."""

from nicegui import ui


def register_file_pages(service, shell, action):
    t = service.translate_ui

    @ui.page("/files")
    def files_page():
        with shell("Arquivos"):
            ui.label(t("Arquivos do Studio")).classes("studio-title")
            ui.label(
                t(
                    "Organize áudios gerados, gravações de referência e legendas. Copiar preserva o original; mover atualiza o histórico e as vozes salvas. O destino pode ser um caminho do Windows, como C:\\Users\\Você\\Music."
                )
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full gap-3"):
                destination = ui.input(t("Pasta de destino"), placeholder="C:\\Users\\Você\\Music\\F5 Studio").props(
                    "outlined"
                )
                ui.label(
                    t(
                        "A cópia ou movimentação só acontece quando você clicar na ação do arquivo. O Studio nunca substitui um arquivo existente; acrescenta um número ao nome quando necessário."
                    )
                ).classes("text-sm studio-muted")

            @ui.refreshable
            def inventory():
                files = service.asset_inventory()
                total = sum(item["size"] for item in files)
                ui.label(
                    t("{value_0} arquivos · {value_1} no total", value_0=len(files), value_1=format_bytes(total))
                ).classes("font-semibold")
                if not files:
                    ui.label(t("Ainda não há arquivos de áudio ou saída para organizar.")).classes("studio-muted")
                    return
                for item in files:
                    with ui.card().classes("studio-card w-full gap-3"):
                        with ui.row().classes("w-full items-center flex-wrap"):
                            ui.badge(t(item["category"]), color="teal-1", text_color="teal-9")
                            ui.label(item["name"]).classes("font-semibold break-all")
                            ui.space()
                            ui.label(format_bytes(item["size"])).classes("text-sm studio-muted")
                        ui.label(item["path"]).classes("text-xs studio-muted break-all")
                        if item["uses"]:
                            for usage in item["uses"]:
                                ui.label(t(usage)).classes("text-sm")
                        else:
                            ui.label(t("Sem vínculo ativo no histórico ou na biblioteca de vozes.")).classes(
                                "text-sm studio-muted"
                            )
                        if item["name"].lower().endswith((".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")):
                            ui.audio(item["path"]).classes("w-full")
                        with ui.row().classes("w-full items-end flex-wrap"):
                            filename = (
                                ui.input(t("Novo nome (opcional)"), value=item["name"])
                                .props("outlined dense")
                                .style("flex:1 1 220px;width:auto")
                            )

                            async def organize(source=item["path"], name=filename, move=False):
                                result = await action(
                                    service.organize_asset, source, destination.value, name.value, move
                                )
                                if result:
                                    ui.notify(
                                        (t("Arquivo movido: ") if move else t("Cópia salva: ")) + result["path"],
                                        type="positive",
                                        close_button=True,
                                        timeout=8000,
                                    )
                                    inventory.refresh()

                            ui.button(
                                t("Copiar para pasta"),
                                icon="content_copy",
                                on_click=lambda source=item["path"], name=filename: organize(source, name, False),
                            ).props("flat no-caps")
                            ui.button(
                                t("Mover para pasta"),
                                icon="drive_file_move",
                                on_click=lambda source=item["path"], name=filename: organize(source, name, True),
                            ).props("outline no-caps")
                            ui.button(
                                t("Baixar arquivo"),
                                icon="download",
                                on_click=lambda path=item["path"]: ui.download.file(path),
                            ).props("flat no-caps")

                        with ui.dialog() as confirm, ui.card().classes("studio-card w-full max-w-xl"):
                            ui.label(t("Apagar este arquivo?")).classes("text-lg font-semibold")
                            ui.label(
                                t(
                                    "O Studio também remove referências do histórico e invalida trechos de narração que dependem deste arquivo. Se for uma voz usada por uma história, você precisará escolher outra voz antes."
                                )
                            ).classes("studio-muted")

                            async def delete(source=item["path"], dialog=confirm):
                                result = await action(service.delete_asset, source)
                                if result is not None:
                                    dialog.close()
                                    inventory.refresh()
                                    ui.notify(
                                        t("Arquivo apagado. Espaço liberado: ") + format_bytes(result), type="positive"
                                    )

                            with ui.row().classes("w-full justify-end"):
                                ui.button(t("Cancelar"), on_click=confirm.close).props("flat no-caps")
                                ui.button(t("Apagar arquivo"), icon="delete", on_click=delete).props(
                                    "color=negative no-caps"
                                )
                        ui.button(t("Apagar"), icon="delete", on_click=confirm.open).props("flat no-caps")

            inventory()


def format_bytes(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
