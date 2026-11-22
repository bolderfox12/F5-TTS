"""Local audio library with waveform previews and non-destructive trimming."""

from pathlib import Path

from nicegui import ui

from f5_tts.studio.montage import asset_kind
from f5_tts.studio.montage_page import send_to_video_editor


def register_file_pages(service, shell, action):
    t = service.translate_ui

    @ui.page("/files")
    def files_page():
        with shell("Biblioteca de áudios"):
            ui.label(t("Biblioteca de áudios")).classes("studio-title")
            ui.label(
                t(
                    "Encontre, ouça, corte, baixe e organize os áudios gerados e suas referências. O corte cria um novo WAV e preserva o arquivo original."
                )
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full gap-3"):
                destination = ui.input(t("Pasta de destino"), placeholder="C:\\Users\\Você\\Music\\F5 Studio").props(
                    "outlined"
                )
                ui.label(
                    t(
                        "Copiar mantém o arquivo original; mover atualiza os vínculos do Studio. O destino pode ser um caminho Windows ou Linux."
                    )
                ).classes("text-sm studio-muted")

            with ui.row().classes("w-full items-end flex-wrap"):
                search = (
                    ui.input(t("Buscar arquivos"), placeholder=t("Nome, referência ou texto gerado"))
                    .props("outlined dense clearable")
                    .style("flex:1 1 260px;width:auto")
                )
                category_filter = ui.select(
                    [
                        "Todos",
                        "Áudio gerado",
                        "Referência salva",
                        "Áudio editado",
                        "Upload de áudio",
                        "Legenda ou texto",
                    ],
                    value="Todos",
                    label=t("Categoria"),
                ).props("outlined dense")

            @ui.refreshable
            def inventory():
                files = service.asset_inventory()
                query = (search.value or "").strip().casefold()
                selected_category = category_filter.value
                files = [
                    item
                    for item in files
                    if (selected_category == "Todos" or item["category"] == selected_category)
                    and (
                        not query
                        or query in item["name"].casefold()
                        or query in item["path"].casefold()
                        or any(query in usage.casefold() for usage in item["uses"])
                    )
                ]
                total = sum(item["size"] for item in files)
                ui.label(
                    t("{value_0} arquivos · {value_1} no total", value_0=len(files), value_1=format_bytes(total))
                ).classes("font-semibold")
                if not files:
                    ui.label(t("Nenhum arquivo corresponde à busca. Confira o Histórico para áudios gerados.")).classes(
                        "studio-muted"
                    )
                    return

                for item in files:
                    is_audio = item["name"].lower().endswith((".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"))
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
                        elif item["category"] == "Upload de áudio":
                            ui.label(t("Referência enviada, ainda não salva em Minhas vozes.")).classes(
                                "text-sm studio-muted"
                            )
                        else:
                            ui.label(t("Sem vínculo ativo no histórico ou na biblioteca de vozes.")).classes(
                                "text-sm studio-muted"
                            )

                        if is_audio:
                            ui.audio(item["path"]).classes("w-full")
                            try:
                                waveform = service.audio_waveform(item["path"])
                            except (OSError, RuntimeError, ValueError):
                                waveform = None
                            if waveform:
                                duration = waveform["duration"]
                                ui.label(
                                    t(
                                        "Forma de onda · {value_0} · {value_1} Hz",
                                        value_0=format_time(duration),
                                        value_1=waveform["sample_rate"],
                                    )
                                ).classes("text-sm font-medium")
                                ui.echart(
                                    {
                                        "animation": False,
                                        "grid": {"left": 2, "right": 2, "top": 4, "bottom": 4},
                                        "tooltip": {"trigger": "axis", "formatter": "{b} s"},
                                        "xAxis": {
                                            "type": "category",
                                            "show": False,
                                            "data": [
                                                round(i * duration / len(waveform["peaks"]), 2)
                                                for i in range(len(waveform["peaks"]))
                                            ],
                                        },
                                        "yAxis": {"type": "value", "show": False, "min": 0, "max": 1},
                                        "series": [
                                            {
                                                "type": "bar",
                                                "data": waveform["peaks"],
                                                "barWidth": "100%",
                                                "itemStyle": {"color": "#137d80", "borderRadius": [2, 2, 0, 0]},
                                            }
                                        ],
                                    }
                                ).classes("w-full").style("height:110px")
                                with ui.expansion(t("Cortar este áudio"), icon="content_cut").classes("w-full"):
                                    ui.label(
                                        t("Escolha o início e o fim abaixo. O original fica guardado na biblioteca.")
                                    ).classes("text-sm studio-muted")
                                    start = ui.slider(min=0, max=duration, value=0, step=0.01).props("label-always")
                                    ui.label(t("Início do trecho (segundos)"))
                                    end = ui.slider(min=0, max=duration, value=duration, step=0.01).props(
                                        "label-always"
                                    )
                                    ui.label(t("Fim do trecho (segundos)"))
                                    trim_result = {}
                                    trim_status = ui.label("").classes("text-sm studio-muted")

                                    async def trim(
                                        source=item["path"],
                                        start_control=start,
                                        end_control=end,
                                        result=trim_result,
                                        status=trim_status,
                                    ):
                                        created = await action(
                                            service.trim_audio, source, start_control.value, end_control.value
                                        )
                                        if created:
                                            result.update(created)
                                            status.set_text(
                                                t(
                                                    "Recorte salvo: {value_0} ({value_1})",
                                                    value_0=Path(created["path"]).name,
                                                    value_1=format_time(created["duration"]),
                                                )
                                            )
                                            inventory.refresh()
                                            ui.notify(
                                                t("Recorte adicionado à biblioteca sem alterar o original."),
                                                type="positive",
                                            )

                                    ui.button(t("Criar recorte WAV"), icon="content_cut", on_click=trim).props(
                                        "unelevated no-caps"
                                    )

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

                            if asset_kind(item["path"]):
                                ui.button(
                                    t("Enviar ao editor de vídeo"),
                                    icon="video_library",
                                    on_click=lambda asset=item: send_to_video_editor(
                                        service,
                                        action,
                                        [(asset["path"], asset["name"])],
                                        asset["name"],
                                    ),
                                ).props("flat no-caps")

                            if is_audio:
                                with ui.dialog() as voice_dialog, ui.card().classes("studio-card w-full max-w-xl"):
                                    ui.label(t("Salvar áudio como voz de referência")).classes("text-lg font-semibold")
                                    voice_name = ui.input(t("Nome da voz")).props("outlined")
                                    voice_transcript = ui.textarea(t("Texto falado no áudio")).props("outlined rows=3")

                                    async def save_reference(
                                        source=item["path"],
                                        dialog=voice_dialog,
                                        name=voice_name,
                                        transcript=voice_transcript,
                                    ):
                                        saved = await action(service.save_voice, name.value, source, transcript.value)
                                        if saved:
                                            dialog.close()
                                            ui.notify(t("Referência adicionada a Minhas vozes."), type="positive")
                                            inventory.refresh()

                                    with ui.row().classes("w-full justify-end"):
                                        ui.button(t("Cancelar"), on_click=voice_dialog.close).props("flat no-caps")
                                        ui.button(
                                            t("Salvar voz"), icon="record_voice_over", on_click=save_reference
                                        ).props("unelevated no-caps")
                                ui.button(
                                    t("Salvar como referência"),
                                    icon="record_voice_over",
                                    on_click=voice_dialog.open,
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

            search.on_value_change(lambda: inventory.refresh())
            category_filter.on_value_change(lambda: inventory.refresh())
            inventory()


def format_bytes(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024


def format_time(seconds):
    total = max(0, int(seconds))
    minutes, remainder = divmod(total, 60)
    return f"{minutes:02d}:{remainder:02d}"
