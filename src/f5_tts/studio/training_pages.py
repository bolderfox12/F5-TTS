"""Training workspace: data review, GPU tuning, checkpoint audition and deployment."""

from datetime import datetime
from functools import partial
from pathlib import Path

from nicegui import run, ui

from f5_tts.studio.gemini_tips import gemini_tips, training_context_json


def register_training_pages(service, shell, action, audio_upload):
    t = service.translate_ui

    @ui.page("/training")
    def training_page():
        uploads = []
        state = {"project": "", "selected": "", "results": []}
        voices = {item["id"]: item["name"] for item in service.read().get("voices", [])}
        with shell("Treinar uma voz"):
            ui.label(t("Treinamento de voz")).classes("studio-title")
            ui.label(t("Do conjunto de gravações ao checkpoint pronto para usar.")).classes("studio-muted")

            with ui.card().classes("studio-card w-full gap-3"):
                ui.label(t("Projeto de treinamento")).classes("font-semibold text-lg")
                with ui.row().classes("w-full items-end gap-3"):
                    project_name = ui.input(t("Nome do projeto"), value="minha_voz").props("outlined")
                    known_projects = service.projects()
                    saved_project = ui.select(
                        known_projects,
                        label=t("Continuar projeto existente"),
                        clearable=True,
                        on_change=lambda event: project_name.set_value(event.value or project_name.value),
                    ).props("outlined")
                    project_label = ui.label(t("Crie um projeto ou abra um existente.")).classes("studio-muted")

                    async def open_project():
                        project = await action(service.project, project_name.value)
                        if not project:
                            return
                        state["project"] = project.name
                        state["selected"] = ""
                        sample_audio.set_source("")
                        quality_label.set_text("")
                        correction.set_value("")
                        approved.set_value(False)
                        project_name.set_value(project.name.removesuffix("_pinyin"))
                        project_label.set_text(t("Projeto aberto: ") + project.name)
                        saved_project.set_options(service.projects())
                        saved_project.set_value(project.name)
                        refresh_rows.refresh()
                        dataset_status.set_content(project.summary())
                        load_options()
                        await refresh_checkpoints()
                        await refresh_gpu()

                    ui.button(t("Abrir projeto"), icon="folder_open", on_click=open_project).props("no-caps unelevated")

            with ui.tabs().classes("w-full") as tabs:
                data_tab = ui.tab("data", label=t("Dados e transcrições"), icon="library_music")
                train_tab = ui.tab("train", label=t("Treino e GPU"), icon="memory")
                eval_tab = ui.tab("evaluate", label=t("Avaliar checkpoints"), icon="compare")
                deploy_tab = ui.tab("deploy", label=t("Usar na inferência"), icon="rocket_launch")
                quantize_tab = ui.tab("quantize", label=t("Quantização"), icon="compress")

            with ui.tab_panels(tabs, value=data_tab).classes("w-full"):
                with ui.tab_panel(data_tab):
                    with ui.card().classes("studio-card w-full gap-4"):
                        ui.label(t("1. Adicione gravações reais da mesma pessoa")).classes("font-semibold text-lg")
                        ui.label(
                            t(
                                "Gravações limpas e variadas ensinam melhor a voz. Os áudios gerados pelo próprio modelo não entram automaticamente no conjunto de treino."
                            )
                        ).classes("studio-muted")
                        import_status = ui.label(t("Nenhum arquivo enviado.")).classes("text-sm")
                        audio_upload(uploads, import_status, multiple=True)
                        language = ui.select(
                            {
                                "pt": t("Português"),
                                "en": t("Inglês"),
                                "es": t("Espanhol"),
                                "fr": t("Francês"),
                                "de": t("Alemão"),
                                "it": "Italiano",
                                "zh": "中文",
                                "ja": "日本語",
                                "ko": "한국어",
                                "ar": "العربية",
                                "ru": "Русский",
                                "hi": "हिन्दी",
                                "vi": "Tiếng Việt",
                                "": t("Detectar idioma"),
                            },
                            value="pt",
                            label=t("Idioma das gravações"),
                        ).props("outlined")
                        with ui.expansion(t("Importar com transcrições prontas"), icon="description").classes("w-full"):
                            transcripts = (
                                ui.textarea(t("Uma linha por áudio curto no formato arquivo.wav|transcrição"))
                                .props("outlined rows=4")
                                .classes("w-full")
                            )
                        consent = ui.checkbox(t("Tenho autorização para usar estas gravações no treinamento"))
                        import_message = ui.label("").classes("whitespace-pre-wrap studio-muted")

                        async def import_recordings():
                            if not state["project"]:
                                ui.notify(t("Abra um projeto antes de importar gravações."), type="warning")
                                return
                            import_button.disable()
                            import_message.set_text(t("Importando e transcrevendo os áudios…"))
                            try:
                                result = await action(
                                    service.import_audio,
                                    state["project"],
                                    list(uploads),
                                    language.value,
                                    consent.value,
                                    transcripts.value,
                                )
                                if result is not None:
                                    import_message.set_text(t(str(result)))
                                    uploads.clear()
                                    transcripts.set_value("")
                                    import_status.set_text(t("Nenhum arquivo enviado."))
                                    refresh_rows.refresh()
                            finally:
                                import_button.enable()

                        import_button = ui.button(
                            t("Importar gravações"), icon="text_snippet", on_click=import_recordings
                        ).props("no-caps unelevated")

                    with ui.card().classes("studio-card w-full gap-4"):
                        ui.label(t("2. Revise cada trecho antes de preparar")).classes("font-semibold text-lg")
                        sample_audio = ui.audio("").classes("w-full")
                        quality_label = ui.label("").classes("studio-muted text-sm")
                        correction = ui.textarea(t("Transcrição exata")).props("outlined rows=3")
                        approved = ui.checkbox(t("Ouvi e aprovei este trecho"))

                        def choose_example(event):
                            if not event.selection:
                                return
                            row = event.selection[0]
                            state["selected"] = row["id"]
                            correction.set_value(row["text"])
                            approved.set_value(row["approved"])
                            level, clipping = row["rms_db"], row["clipped_percent"]
                            quality_label.set_text(
                                t("Nível RMS: {level} dBFS · clipping: {clipping}%", level=level, clipping=clipping)
                                if level is not None
                                else ""
                            )
                            project = service.project(state["project"])
                            sample_audio.set_source(str(project.path / "wavs" / (row["id"] + ".wav")))

                        @ui.refreshable
                        def refresh_rows():
                            if not state["project"]:
                                ui.label(t("Abra um projeto para revisar os dados."))
                                return
                            project = service.project(state["project"])
                            examples = project.read()["examples"]
                            accepted = [example for example in examples if example["approved"]]
                            ui.label(
                                t(
                                    "{value_0} trechos · {value_1} aprovados · {value_2} segundos aprovados",
                                    value_0=len(examples),
                                    value_1=len(accepted),
                                    value_2=round(sum(example["duration"] for example in accepted), 1),
                                )
                            ).classes("studio-muted")
                            ui.table(
                                columns=[
                                    {"name": "source", "label": t("Arquivo"), "field": "source", "align": "left"},
                                    {"name": "text", "label": t("Texto"), "field": "text", "align": "left"},
                                    {"name": "approved", "label": t("Aprovado"), "field": "approved"},
                                    {"name": "seconds", "label": t("Duração (s)"), "field": "seconds"},
                                ],
                                rows=[
                                    {
                                        "id": row["id"],
                                        "approved": row["approved"],
                                        "text": row["text"],
                                        "source": row.get("source", "—"),
                                        "seconds": round(row["duration"], 2),
                                        "rms_db": row.get("rms_db"),
                                        "clipped_percent": row.get("clipped_percent"),
                                    }
                                    for row in examples
                                ],
                                row_key="id",
                                selection="single",
                                on_select=choose_example,
                                pagination=10,
                            ).classes("w-full")

                        refresh_rows()

                        async def save_review():
                            result = await action(
                                service.review,
                                state["project"],
                                state["selected"],
                                correction.value,
                                approved.value,
                            )
                            if result is not None:
                                refresh_rows.refresh()

                        extend_vocab = ui.checkbox(t("Ampliar vocabulário com tokens dos textos aprovados"), value=True)

                        async def prepare_dataset():
                            if not state["project"]:
                                ui.notify(t("Abra um projeto para revisar os dados."), type="warning")
                                return
                            result = await action(
                                service.prepare, state["project"], base_vocabulary.value or "", extend_vocab.value
                            )
                            if result:
                                dataset_status.set_content(result)
                                ui.notify(t("Dados preparados para treinamento."), type="positive")

                        with ui.row():
                            ui.button(t("Salvar revisão"), icon="check", on_click=save_review).props("no-caps")
                            ui.button(t("Preparar conjunto aprovado"), icon="dataset", on_click=prepare_dataset).props(
                                "no-caps unelevated"
                            )
                        dataset_status = ui.markdown(
                            t("A preparação verifica transcrições, arquivos e vocabulário.")
                        ).classes("studio-muted whitespace-pre-wrap")

                with ui.tab_panel(train_tab):
                    with ui.card().classes("studio-card w-full gap-4"):
                        ui.label(t("Monitor de recursos")).classes("font-semibold text-lg")
                        resource_status = ui.label("").classes("studio-muted whitespace-pre-wrap")

                        with ui.row().classes("w-full gap-4"):
                            usage_chart = ui.echart(
                                {
                                    "animation": False,
                                    "tooltip": {"trigger": "axis"},
                                    "legend": {"type": "scroll", "bottom": 0},
                                    "grid": {"left": 48, "right": 15, "top": 20, "bottom": 50},
                                    "xAxis": {"type": "category", "boundaryGap": False, "data": []},
                                    "yAxis": {"type": "value", "max": 100, "axisLabel": {"formatter": "{value}%"}},
                                    "series": [],
                                }
                            ).classes("h-64 grow min-w-[280px]")
                            disk_chart = ui.echart(
                                {
                                    "animation": False,
                                    "tooltip": {"trigger": "axis"},
                                    "legend": {"bottom": 0},
                                    "grid": {"left": 55, "right": 15, "top": 26, "bottom": 50},
                                    "xAxis": {"type": "category", "boundaryGap": False, "data": []},
                                    "yAxis": {"type": "value", "name": "MiB/s", "min": 0},
                                    "series": [],
                                }
                            ).classes("h-64 grow min-w-[280px]")
                        resource_samples = []

                        async def refresh_gpu():
                            if tabs.value not in (train_tab, train_tab.props["name"]):
                                return
                            metrics = await action(service.system_metrics, True)
                            if metrics is None:
                                return
                            values = {
                                t("CPU"): metrics["cpu_percent"],
                                t("RAM"): metrics["ram_percent"],
                                t("Ocupação do SSD"): metrics["disk_used_percent"],
                            }
                            disk_values = {
                                t("Leitura do sistema"): round(metrics["disk_read_mbps"], 1),
                                t("Gravação do sistema"): round(metrics["disk_write_mbps"], 1),
                            }
                            details = [
                                f"CPU {metrics['cpu_percent']:.0f}% · RAM {metrics['ram_used_gb']:.1f}/"
                                f"{metrics['ram_total_gb']:.1f} GiB · SSD {metrics['disk_free_gb']:.1f} GiB {t('livre')}"
                            ]
                            gpus = metrics["gpus"]
                            multi_gpu.set_enabled(len(gpus) >= 2 or bool(multi_gpu.value))
                            if not gpus:
                                details.append(
                                    t(
                                        "CUDA indisponível. Os dados podem ser preparados, mas o treino precisa de uma GPU NVIDIA."
                                    )
                                )
                            for gpu in gpus:
                                label = f"GPU {gpu['index']}"
                                values[label] = gpu["utilization"]
                                used = gpu["used_gib"]
                                total = gpu["total_gib"]
                                vram = f"{label} · VRAM"
                                values[vram] = round(100 * used / total, 1) if used is not None and total else None
                                utilization = f"{gpu['utilization']:.0f}%" if gpu["utilization"] is not None else "—"
                                memory = f"{used:.1f}/{total:.1f} GiB" if used is not None else "—"
                                status = f"{label} · {gpu['name']} · {utilization} · VRAM {memory}"
                                if gpu["temperature"] is not None:
                                    status += f" · {gpu['temperature']:.0f} °C"
                                if gpu["power"] is not None:
                                    status += f" · {gpu['power']:.0f} W"
                                details.append(status)
                            resource_status.set_text("\n".join(details))
                            resource_samples.append((datetime.now().strftime("%H:%M:%S"), values, disk_values))
                            del resource_samples[:-60]
                            for chart, series_index, current in (
                                (usage_chart, 1, values),
                                (disk_chart, 2, disk_values),
                            ):
                                chart.options["xAxis"]["data"] = [sample[0] for sample in resource_samples]
                                chart.options["series"] = [
                                    {
                                        "name": series,
                                        "type": "line",
                                        "showSymbol": False,
                                        "data": [sample[series_index].get(series) for sample in resource_samples],
                                    }
                                    for series in current
                                ]
                                chart.update()

                        ui.timer(2, refresh_gpu)

                    with ui.card().classes("studio-card w-full gap-4"):
                        ui.label(t("Modelo inicial e vocabulário")).classes("font-semibold text-lg")
                        ui.label(
                            t(
                                "Envie um checkpoint F5 compatível para continuar o treino. Se deixar vazio, o treino parte do modelo base e pode baixar cerca de 1,4 GB na primeira execução."
                            )
                        ).classes("studio-muted")
                        base_checkpoint = ui.input(t("Checkpoint inicial (.pt / .safetensors)"), value="").props(
                            "outlined"
                        )
                        base_vocabulary = ui.input(t("Vocabulário do modelo (.txt, opcional)"), value="").props(
                            "outlined"
                        )

                        async def upload_weight(event, target):
                            try:
                                limit = 5_000_000 if target is base_vocabulary else 5_000_000_000
                                if event.file.size() > limit:
                                    raise ValueError("O arquivo excede o tamanho permitido.")
                                path = service.model_upload_path(event.file.name)
                                await event.file.save(path)
                                target.set_value(str(path))
                                ui.notify(t("Arquivo salvo no armazenamento local do Studio."), type="positive")
                            except Exception as error:
                                ui.notify(t(str(error)), type="negative")

                        with ui.row().classes("w-full flex-wrap"):
                            ui.upload(
                                label=t("Enviar checkpoint"),
                                auto_upload=True,
                                max_files=1,
                                max_file_size=5_000_000_000,
                                on_upload=lambda event: upload_weight(event, base_checkpoint),
                            ).props('accept=".pt,.pth,.ckpt,.safetensors"').classes("flex-1")
                            ui.upload(
                                label=t("Enviar vocabulário"),
                                auto_upload=True,
                                max_files=1,
                                max_file_size=5_000_000,
                                on_upload=lambda event: upload_weight(event, base_vocabulary),
                            ).props('accept=".txt"').classes("flex-1")
                        architecture = ui.select(
                            {
                                "F5TTS_v1_Base": "F5-TTS v1 Base",
                                "F5TTS_Base": "F5-TTS Base",
                                "F5TTS_Base_bigvgan": "F5-TTS Base · BigVGAN",
                                "E2TTS_Base": "E2-TTS Base",
                            },
                            value="F5TTS_v1_Base",
                            label=t("Arquitetura do checkpoint"),
                        ).props("outlined")

                    with ui.card().classes("studio-card w-full gap-4"):
                        ui.label(t("Configuração para esta GPU")).classes("font-semibold text-lg")
                        ui.label(
                            t(
                                "O perfil sugerido usa precisão mista e tamanho de lote calculado pela VRAM. Reduza o lote se ocorrer falta de memória; use acumulação de gradiente para recuperar o tamanho efetivo."
                            )
                        ).classes("studio-muted")
                        precision = ui.select(
                            {"bf16": "BF16", "fp16": "FP16", "none": t("Precisão total")},
                            value="fp16",
                            label=t("Precisão numérica"),
                        ).props("outlined")
                        use_8bit = ui.switch(t("Otimizador Adam de 8 bits · reduz memória da GPU"), value=False)
                        activation_checkpointing = ui.switch(t("Recomputar ativações · economiza VRAM no F5-TTS"))
                        tf32 = ui.switch(t("TF32 · acelera operações em GPUs NVIDIA Ampere ou mais novas"))
                        log_samples = ui.switch(t("Gerar áudios de amostra ao salvar checkpoints"))
                        multi_gpu = ui.switch(t("Usar múltiplas GPUs CUDA")).set_enabled(False)
                        ui.label(
                            t(
                                "Desativado: usa a primeira GPU visível. Ativado: distribui o treino pelas GPUs CUDA visíveis."
                            )
                        ).classes("studio-muted text-sm")
                        cache_in_ram = ui.switch(t("Cache de espectrogramas em RAM"))
                        cache_budget = (
                            ui.number(t("Limite total de cache (GiB)"), value=4.0, min=0.25, max=1024, step=0.25)
                            .props("outlined")
                            .bind_visibility_from(cache_in_ram, "value")
                        )
                        ui.label(
                            t(
                                "Reutiliza espectrogramas entre épocas. O Studio divide o limite entre as GPUs e ajusta a leitura para usar o cache."
                            )
                        ).classes("studio-muted text-sm").bind_visibility_from(cache_in_ram, "value")
                        with ui.expansion(t("Controles avançados de desempenho e treino"), icon="tune").classes(
                            "w-full"
                        ):
                            batch_size = ui.number(
                                t("Orçamento do lote (frames por GPU)"), value=3200, min=400, max=50000, step=256
                            ).props("outlined")
                            max_samples = ui.number(
                                t("Máximo de exemplos por lote"), value=8, min=1, max=256, step=1
                            ).props("outlined")
                            grad_accumulation = ui.number(
                                t("Acumulação de gradiente"), value=1, min=1, max=64, step=1
                            ).props("outlined")
                            max_grad_norm = ui.number(
                                t("Limite de clipping do gradiente"), value=1.0, min=0.1, max=10, step=0.1
                            ).props("outlined")
                            num_workers = ui.number(
                                t("Workers de leitura de dados na CPU"), value=8, min=0, max=32, step=1
                            ).props("outlined")
                            learning_rate = ui.number(
                                t("Taxa de aprendizado"), value=0.00001, min=0.0000001, max=0.001, step=0.000001
                            ).props("outlined")
                            epochs = ui.number(t("Épocas"), value=20, min=1, max=10000, step=1).props("outlined")
                            warmup = ui.number(
                                t("Atualizações de warmup"), value=100, min=0, max=100000, step=10
                            ).props("outlined")
                            save_every = ui.number(
                                t("Salvar checkpoint a cada atualizações"), value=100, min=10, max=100000, step=10
                            ).props("outlined")
                            keep_checkpoints = ui.number(
                                t("Manter checkpoints recentes"), value=5, min=1, max=1000, step=1
                            ).props("outlined")

                            seed = ui.number(t("Semente do treino"), value=666, min=0, max=4294967295, step=1).props(
                                "outlined"
                            )
                            validation = ui.number(
                                t("Reserva para validação (%)"), value=0, min=0, max=30, step=1
                            ).props("outlined")

                        async def apply_gpu_profile():
                            profile = await action(service.gpu_training_recommendation)
                            if not profile:
                                return
                            batch_size.set_value(profile["batch_size"])
                            max_samples.set_value(profile["max_samples"])
                            grad_accumulation.set_value(profile["grad_accumulation"])
                            max_grad_norm.set_value(profile["max_grad_norm"])
                            num_workers.set_value(profile["num_workers"])
                            precision.set_value(profile["precision"])
                            use_8bit.set_value(profile["use_8bit_optimizer"])
                            activation_checkpointing.set_value(
                                profile["activation_checkpointing"] and architecture.value != "E2TTS_Base"
                            )
                            tf32.set_value(profile["tf32"])
                            ui.notify(t("Perfil ajustado para ") + profile["hardware"]["name"], type="positive")

                        def load_options():
                            if not state["project"]:
                                return
                            options = service.training_options(state["project"])
                            architecture.set_value(options["architecture"])
                            base_checkpoint.set_value(options["base_checkpoint"])
                            base_vocabulary.set_value(options["vocabulary"])
                            precision.set_value(options["precision"])
                            use_8bit.set_value(options["use_8bit_optimizer"])
                            activation_checkpointing.set_value(options["activation_checkpointing"])
                            tf32.set_value(options["tf32"])
                            log_samples.set_value(options["log_samples"])
                            seed.set_value(options["seed"])
                            validation.set_value(options["validation_percent"])
                            multi_gpu.set_value(options["multi_gpu"])
                            cache_in_ram.set_value(options["ram_cache_gib"] > 0)
                            cache_budget.set_value(options["ram_cache_gib"] or 4.0)
                            batch_size.set_value(options["batch_size"])
                            max_samples.set_value(options["max_samples"])
                            grad_accumulation.set_value(options["grad_accumulation"])
                            max_grad_norm.set_value(options["max_grad_norm"])
                            num_workers.set_value(options["num_workers"])
                            learning_rate.set_value(options["learning_rate"])
                            epochs.set_value(options["epochs"])
                            warmup.set_value(options["warmup_updates"])
                            save_every.set_value(options["save_every"])
                            keep_checkpoints.set_value(options["keep_checkpoints"])

                        def collect_options():
                            return {
                                "architecture": architecture.value,
                                "base_checkpoint": base_checkpoint.value or "",
                                "vocabulary": base_vocabulary.value or "",
                                "precision": precision.value,
                                "use_8bit_optimizer": use_8bit.value,
                                "activation_checkpointing": activation_checkpointing.value,
                                "tf32": tf32.value,
                                "log_samples": log_samples.value,
                                "seed": seed.value,
                                "validation_percent": validation.value,
                                "multi_gpu": multi_gpu.value,
                                "ram_cache_gib": cache_budget.value if cache_in_ram.value else 0.0,
                                "batch_size": batch_size.value,
                                "max_samples": max_samples.value,
                                "grad_accumulation": grad_accumulation.value,
                                "max_grad_norm": max_grad_norm.value,
                                "num_workers": num_workers.value,
                                "learning_rate": learning_rate.value,
                                "epochs": epochs.value,
                                "warmup_updates": warmup.value,
                                "save_every": save_every.value,
                                "keep_checkpoints": keep_checkpoints.value,
                            }

                        async def save_options():
                            result = await action(service.save_training_options, state["project"], collect_options())
                            if result:
                                ui.notify(t("Configuração salva neste projeto."), type="positive")

                        async def collect_training_tips(_topic):
                            return await training_context_json(
                                service,
                                project=state["project"],
                                options=collect_options() if state["project"] else None,
                            )

                        gemini_tips(service, collect_training_tips, topics=("training",))

                        async def start_training():
                            if not state["project"]:
                                ui.notify(t("Abra um projeto antes de iniciar o treino."), type="warning")
                                return
                            start_button.disable()
                            try:
                                result = await action(service.start_training, state["project"], collect_options())
                                if result is not None:
                                    ui.notify(
                                        t("Treinamento iniciado. Acompanhe GPU, andamento e checkpoints nesta tela."),
                                        type="positive",
                                    )
                            finally:
                                start_button.enable()

                        with ui.row().classes("w-full flex-wrap"):
                            ui.button(t("Otimizar para esta GPU"), icon="bolt", on_click=apply_gpu_profile).props(
                                "outline no-caps"
                            )
                            ui.button(t("Salvar configuração"), icon="save", on_click=save_options).props("no-caps")
                            start_button = ui.button(
                                t("Iniciar ou retomar treino"), icon="play_arrow", on_click=start_training
                            ).props("no-caps unelevated")
                            ui.button(
                                t("Parar com segurança"), icon="stop", on_click=lambda: action(service.stop_training)
                            ).props("outline no-caps")
                        job_status = ui.label(service.job["message"]).classes(
                            "whitespace-pre-wrap w-full rounded-lg bg-slate-100 p-4 text-sm"
                        )
                        ui.label(t("Progresso do treinamento")).classes("font-semibold")
                        progress_bar = ui.linear_progress(value=0, show_value=False).props("rounded")
                        progress_description = ui.label("").classes("studio-muted text-sm")
                        job_log = ui.log(max_lines=80).classes("w-full h-48 text-sm")
                        last_log = []

                        def update_job():
                            nonlocal last_log
                            job = service.job
                            job_status.set_text(t(job["message"]))
                            progress = job.get("progress")
                            progress_bar.props(
                                "indeterminate" if job["running"] and not progress else "",
                                remove="indeterminate" if progress or not job["running"] else "",
                            )
                            progress_bar.set_value(min(1, progress[0] / progress[1]) if progress and progress[1] else 0)
                            if progress:
                                completed, total, epoch, epochs = progress
                                progress_description.set_text(
                                    t(
                                        "{percent}% · Época {epoch}/{epochs} · {done}/{total} atualizações",
                                        percent=min(100, round(100 * completed / max(1, total))),
                                        epoch=epoch,
                                        epochs=epochs,
                                        done=completed,
                                        total=total,
                                    )
                                )
                            else:
                                progress_description.set_text("")
                            messages = list(job.get("log", ()))
                            if messages != last_log:
                                job_log.clear()
                                for message in messages[-80:]:
                                    if message:
                                        job_log.push(message)
                                last_log = messages

                        ui.timer(1, update_job)
                        ui.label(
                            t(
                                "O modelo original e seus outros projetos permanecem intactos. O treino continua no último checkpoint deste projeto."
                            )
                        ).classes("text-sm studio-muted")

                with ui.tab_panel(eval_tab):
                    with ui.card().classes("studio-card w-full gap-4"):
                        ui.label(t("Comparação justa entre checkpoints")).classes("font-semibold text-lg")
                        ui.label(
                            t(
                                "Use a mesma referência e a mesma frase. Marque várias versões para gerar uma sequência de amostras comparáveis."
                            )
                        ).classes("studio-muted")
                        checkpoints = ui.select({}, label=t("Checkpoints do projeto"), multiple=True).props(
                            "outlined use-chips"
                        )

                        async def refresh_checkpoints():
                            if not state["project"]:
                                return
                            found = await action(service.checkpoints, state["project"])
                            if found is not None:
                                checkpoints.set_options({path: Path(path).name for path in found})
                                available_checkpoint.set_options({path: Path(path).name for path in found})
                                quant_checkpoint.set_options({path: Path(path).name for path in found})
                                if found:
                                    ui.notify(
                                        t("{value_0} checkpoints encontrados.", value_0=len(found)), type="positive"
                                    )

                        with ui.row():
                            ui.button(t("Atualizar checkpoints"), icon="refresh", on_click=refresh_checkpoints).props(
                                "flat no-caps"
                            )
                            ui.button(
                                t("Selecionar todos"),
                                icon="select_all",
                                on_click=lambda: checkpoints.set_value(list(checkpoints.options)),
                            ).props("flat no-caps")
                        voice = ui.select(voices, label=t("Usar referência de voz salva"), clearable=True).props(
                            "outlined"
                        )
                        reference_paths = []
                        reference_status = ui.label(t("Ou envie uma referência para esta comparação.")).classes(
                            "studio-muted text-sm"
                        )
                        audio_upload(reference_paths, reference_status, reference=True)
                        reference_text = ui.textarea(t("Transcrição da referência (opcional)")).props("outlined rows=2")
                        test_text = ui.textarea(
                            t("Texto fixo para comparar todas as versões"),
                            value=t("Olá! Esta é uma comparação da minha voz."),
                        ).props("outlined rows=3")
                        comparison_status = ui.label("").classes("whitespace-pre-wrap studio-muted")
                        result_area = ui.column().classes("w-full gap-3")

                        async def compare():
                            if not state["project"] or not checkpoints.value:
                                ui.notify(t("Abra um projeto e selecione pelo menos um checkpoint."), type="warning")
                                return
                            selected_voice = next(
                                (item for item in service.read()["voices"] if item["id"] == voice.value), None
                            )
                            ref = (
                                selected_voice["reference"]
                                if selected_voice
                                else (reference_paths[-1] if reference_paths else None)
                            )
                            transcript = selected_voice["transcript"] if selected_voice else reference_text.value
                            if not ref:
                                ui.notify(t("Escolha ou envie uma referência de voz."), type="warning")
                                return
                            result_area.clear()
                            state["results"] = []
                            compare_button.disable()
                            try:
                                selected = list(checkpoints.value)
                                for index, path in enumerate(selected, 1):
                                    comparison_status.set_text(
                                        t(
                                            "Gerando {value_0} de {value_1}: {value_2}",
                                            value_0=index,
                                            value_1=len(selected),
                                            value_2=Path(path).name,
                                        )
                                    )
                                    entry = await run.io_bound(
                                        partial(
                                            service.generate,
                                            test_text.value,
                                            ref,
                                            transcript or "",
                                            None,
                                            state["project"],
                                            path,
                                        )
                                    )
                                    state["results"].append(entry)
                                    with result_area:
                                        with ui.card().classes("studio-card w-full"):
                                            ui.label(Path(path).name).classes("font-semibold")
                                            ui.audio(entry["path"]).classes("w-full")
                                            rating = ui.radio(
                                                {"Ficou bom": t("Ficou bom"), "Precisa melhorar": t("Precisa melhorar")}
                                            )
                                            note = ui.input(t("Observação")).props("outlined")

                                            async def save_rating(item=entry, rate=rating, comment=note):
                                                if rate.value:
                                                    await action(
                                                        service.rate, item["id"], rate.value, comment.value or ""
                                                    )

                                            ui.button(
                                                t("Salvar avaliação"), icon="thumb_up", on_click=save_rating
                                            ).props("flat no-caps")
                                            reference_name = ui.input(
                                                t("Nome da nova referência de estilo"),
                                                value="Estilo · " + Path(path).stem,
                                            ).props("outlined")

                                            async def save_reference(item=entry, name=reference_name):
                                                result = await action(
                                                    service.save_generated_reference,
                                                    name.value,
                                                    item["path"],
                                                    item["text"],
                                                )
                                                if result:
                                                    ui.notify(t("Referência salva em Minhas vozes."), type="positive")

                                            ui.button(
                                                t("Salvar como referência de estilo"),
                                                icon="record_voice_over",
                                                on_click=save_reference,
                                            ).props("flat no-caps")
                                comparison_status.set_text(
                                    t("Comparação concluída. Ouça as versões lado a lado e registre sua avaliação.")
                                )
                            finally:
                                compare_button.enable()

                        compare_button = ui.button(t("Gerar comparação"), icon="graphic_eq", on_click=compare).props(
                            "no-caps unelevated"
                        )

                with ui.tab_panel(deploy_tab):
                    with ui.card().classes("studio-card w-full gap-4"):
                        ui.label(t("Publicar um checkpoint para usar em Criar áudio")).classes("font-semibold text-lg")
                        ui.label(
                            t(
                                "Isso cadastra o arquivo na biblioteca local e preserva o projeto de treinamento. Não copia os pesos novamente."
                            )
                        ).classes("studio-muted")
                        available_checkpoint = ui.select({}, label=t("Checkpoint aprovado")).props("outlined")
                        model_name = ui.input(t("Nome para aparecer em Meus modelos")).props("outlined")

                        async def publish():
                            if not state["project"] or not available_checkpoint.value:
                                ui.notify(t("Abra o projeto e selecione um checkpoint."), type="warning")
                                return
                            result = await action(
                                service.register_project_checkpoint,
                                model_name.value,
                                state["project"],
                                available_checkpoint.value,
                                architecture.value,
                            )
                            if result:
                                ui.notify(
                                    t("Checkpoint cadastrado em Meus modelos e disponível para inferência."),
                                    type="positive",
                                )

                        ui.button(t("Disponibilizar para inferência"), icon="rocket_launch", on_click=publish).props(
                            "no-caps unelevated"
                        )
                        ui.button(
                            t("Abrir Meus modelos"), icon="inventory_2", on_click=lambda: ui.navigate.to("/models")
                        ).props("flat no-caps")

                with ui.tab_panel(quantize_tab):
                    with ui.card().classes("studio-card w-full gap-4"):
                        from f5_tts.studio.model_pages import format_bytes

                        ui.label(t("Exportar checkpoint INT8 para CPU")).classes("font-semibold text-lg")
                        ui.label(
                            t(
                                "O Studio quantiza as camadas lineares do modelo e cadastra a cópia para inferência em CPU x86."
                            )
                        ).classes("studio-muted")
                        quant_checkpoint = ui.select({}, label=t("Checkpoint do projeto")).props("outlined")
                        quant_name = ui.input(t("Nome da cópia INT8")).props("outlined")
                        quant_message = ui.label("").classes("studio-muted")

                        async def quantize():
                            if not state["project"] or not quant_checkpoint.value:
                                ui.notify(t("Abra o projeto e selecione um checkpoint."), type="warning")
                                return
                            quant_button.disable()
                            quant_message.set_text(t("Quantizando pesos…"))
                            try:
                                result = await action(
                                    service.quantize_model,
                                    "",
                                    quant_name.value,
                                    state["project"],
                                    quant_checkpoint.value,
                                )
                                if result:
                                    quant_message.set_text(
                                        t(
                                            "Modelo cadastrado · {before} → {after}",
                                            before=format_bytes(result["source_size"]),
                                            after=format_bytes(result["size"]),
                                        )
                                    )
                                    ui.download.file(result["checkpoint"], filename="modelo-int8.safetensors")
                                else:
                                    quant_message.set_text("")
                            finally:
                                quant_button.enable()

                        quant_button = ui.button(t("Quantizar e baixar"), icon="compress", on_click=quantize).props(
                            "no-caps unelevated"
                        )
                        ui.button(
                            t("Abrir Meus modelos"), icon="inventory_2", on_click=lambda: ui.navigate.to("/models")
                        )
