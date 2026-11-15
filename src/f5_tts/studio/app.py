"""NiceGUI desktop-browser studio. Run with f5-tts_studio."""

import argparse
from pathlib import Path

from nicegui import app, run, ui

from f5_tts.studio.local_pages import register_local_pages
from f5_tts.studio.service import StudioService


CSS = """
body { background: #f3f6f8; color: #172c37; font-family: Inter, system-ui, sans-serif; }
.q-drawer { background: #102e38; color: #d2e5e8; }
.studio-card { background: white; border: 1px solid #e0e8ec; border-radius: 18px; box-shadow: none; padding: 24px; }
.studio-hero { background: linear-gradient(110deg,#113540,#176068); color: white; border-radius: 20px; padding: 32px; }
.studio-title { font-size: 30px; font-weight: 700; letter-spacing: -1px; }
.studio-muted { color: #627781; }
.studio-nav { width: 100%; justify-content: flex-start; padding: 12px 16px; border-radius: 10px; }
.q-stepper { box-shadow: none; border: 1px solid #e0e8ec; border-radius: 18px; width: 100%; }
.q-uploader { box-shadow: none; border: 1px dashed #75a9ad; width: 100%; }
.q-field { width: 100%; }
@media(max-width:700px) { .studio-card,.studio-hero { padding: 18px; } .studio-title {font-size:25px;} }
"""


def register_pages(service):
    app.on_shutdown(service.stop_training)

    async def action(fn, *args):
        try:
            return await run.io_bound(fn, *args)
        except Exception as error:
            ui.notify(str(error), type="negative", close_button=True, timeout=10000)
            return None

    def shell(section):
        ui.colors(primary="#137d80", secondary="#294d5b", accent="#2bb5a7")
        ui.add_css(CSS)
        with ui.left_drawer(value=True).props("show-if-above bordered").classes("p-5") as drawer:
            ui.label("F5 / STUDIO").classes("text-xl font-bold mt-4")
            ui.label("Seu espaço de voz").classes("text-sm opacity-70 mb-8")
            for title, icon, route in [
                ("Criar áudio", "graphic_eq", "/"),
                ("Minhas vozes", "record_voice_over", "/voices"),
                ("Projetos de narração", "article", "/projects"),
                ("Transcrição e legendas", "subtitles", "/transcription"),
                ("Regravar áudio", "mic", "/revoice"),
                ("Ferramentas de áudio", "equalizer", "/audio-tools"),
                ("Treinar uma voz", "school", "/training"),
                ("Histórico", "history", "/history"),
                ("Configurações", "tune", "/settings"),
            ]:
                ui.button(title, icon=icon, on_click=lambda target=route: ui.navigate.to(target)).props(
                    "flat no-caps"
                ).classes("studio-nav")
            ui.space()
            ui.label("EXECUÇÃO LOCAL").classes("text-xs tracking-widest opacity-60")
            ui.label("Os modelos são carregados quando você precisa deles.").classes("text-sm opacity-75")
        with ui.header().classes("bg-white text-slate-700 border-b items-center"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round")
            ui.label(section).classes("font-semibold")
            ui.space()
            ui.badge("Workspace local", color="teal-1", text_color="teal-9")
        return ui.column().classes("w-full max-w-6xl mx-auto p-4 md:p-8 gap-6")

    async def receive(event, paths, label):
        try:
            path = service.upload_path(event.file.name)
            await event.file.save(path)
            paths.append(str(path))
            label.set_text(f"{len(paths)} arquivo(s) enviado(s).")
        except Exception as error:
            ui.notify(str(error), type="negative")

    def audio_upload(paths, label, multiple=False):
        ui.upload(
            label="Arraste os áudios ou clique para escolher",
            multiple=multiple,
            auto_upload=True,
            max_file_size=100_000_000,
            max_files=100 if multiple else 1,
            on_upload=lambda event: receive(event, paths, label),
            on_rejected=lambda: ui.notify("Arquivo recusado. O limite é 100 MB por arquivo.", type="warning"),
        ).props('accept=".wav,.mp3,.flac,.ogg,.m4a,.aac"').classes("w-full")

    register_local_pages(service, shell, action, audio_upload)

    @ui.page("/")
    def create_audio():
        paths, generated = [], {}
        with shell("Criar áudio"):
            with ui.column().classes("studio-hero w-full gap-2"):
                ui.label("DÊ VOZ ÀS SUAS IDEIAS").classes("text-xs tracking-widest opacity-75")
                ui.label("Transforme texto em voz.").classes("studio-title")
                ui.label("Escolha uma referência, escreva seu roteiro e ouça o resultado.").classes("opacity-85")
            with ui.row().classes("w-full gap-6 items-start flex-wrap md:flex-nowrap"):
                with ui.card().classes("studio-card w-full md:w-2/3 gap-4"):
                    ui.label("01 / A voz").classes("font-semibold text-lg")
                    voices = {v["id"]: v["name"] for v in service.read()["voices"]}
                    voice = ui.select(voices, label="Voz salva (opcional)", clearable=True)
                    ui.label("Ou envie uma referência curta. Prefira até 12 segundos, sem música.").classes(
                        "studio-muted"
                    )
                    upload_status = ui.label("Nenhum arquivo enviado.").classes("text-sm")
                    audio_upload(paths, upload_status)
                    reference_text = ui.textarea(
                        label="O que é dito na referência? (opcional)", placeholder="Vazio: transcrição automática."
                    ).props("outlined rows=2")
                    with ui.expansion("Usar um modelo que eu treinei", icon="school").classes("w-full"):
                        trained_project = ui.select(
                            service.projects(), label="Projeto treinado (opcional)", clearable=True
                        ).props("outlined")
                        trained_checkpoint = ui.select({}, label="Checkpoint para usar").props("outlined")

                        async def choose_model():
                            trained_checkpoint.set_value(None)
                            if not trained_project.value:
                                trained_checkpoint.set_options({})
                                return
                            models = await action(service.checkpoints, trained_project.value)
                            if models is not None:
                                trained_checkpoint.set_options({model: Path(model).name for model in models})
                                if not models:
                                    ui.notify(
                                        "O projeto ainda não tem checkpoints. Conclua um treino primeiro.", type="info"
                                    )

                        trained_project.on_value_change(choose_model)
                        ui.label("Sem projeto selecionado, é usado o modelo original.").classes("studio-muted text-sm")
                    ui.separator()
                    ui.label("02 / O roteiro").classes("font-semibold text-lg")
                    text = ui.textarea(label="Texto para narrar", placeholder="Digite ou cole seu texto aqui…").props(
                        "outlined rows=7"
                    )
                    status = ui.label("Pronto para começar.").classes("studio-muted")

                    async def generate():
                        button.disable()
                        status.set_text("Gerando… No primeiro uso, os modelos serão baixados. Aguarde.")
                        try:
                            result = await action(
                                service.generate,
                                text.value,
                                paths[-1] if paths else None,
                                reference_text.value,
                                voice.value,
                                trained_project.value,
                                trained_checkpoint.value,
                            )
                            if result:
                                generated.update(result)
                                player.set_source(result["path"])
                                download.enable()
                                status.set_text("Áudio pronto. Você também pode encontrá-lo no Histórico.")
                            else:
                                status.set_text("Geração não concluída. Confira a mensagem e tente novamente.")
                        finally:
                            button.enable()

                    button = (
                        ui.button("Gerar áudio", icon="play_arrow", on_click=generate)
                        .props("no-caps unelevated")
                        .classes("w-full py-3")
                    )
                with ui.column().classes("w-full md:w-1/3 gap-5"):
                    with ui.card().classes("studio-card w-full"):
                        ui.label("03 / Resultado").classes("font-semibold text-lg")
                        player = ui.audio("").classes("w-full")
                        download = ui.button(
                            "Baixar WAV", icon="download", on_click=lambda: ui.download.file(generated["path"])
                        ).props("flat no-caps")
                        download.disable()
                    with ui.card().classes("studio-card w-full"):
                        ui.icon("lightbulb", color="primary", size="28px")
                        ui.label("Uma boa referência faz diferença.").classes("font-semibold")
                        ui.label(
                            "Use uma gravação limpa, com voz natural e volume confortável. Para personalizar o modelo, abra Treinar uma voz."
                        ).classes("studio-muted")
                        ui.label(
                            "O modelo original é voltado a inglês e chinês; português depende do checkpoint e dos dados."
                        ).classes("text-xs studio-muted")

    @ui.page("/voices")
    def voices_page():
        paths = []
        with shell("Minhas vozes"):
            ui.label("Suas vozes, sempre à mão.").classes("studio-title")
            ui.label("Uma voz salva guarda sua referência e transcrição. Isso não treina nem altera o modelo.").classes(
                "studio-muted"
            )
            with ui.card().classes("studio-card w-full"):
                name = ui.input("Nome da voz", placeholder="Ex.: Minha narração").props("outlined")
                upload_status = ui.label("Envie uma gravação curta.")
                audio_upload(paths, upload_status)
                transcript = ui.textarea("Transcrição da referência (opcional)").props("outlined rows=2")
                consent = ui.checkbox("Tenho autorização para usar esta gravação")

                async def save():
                    if not consent.value:
                        ui.notify("Confirme a autorização para usar a gravação.", type="warning")
                        return
                    result = await action(service.save_voice, name.value, paths[-1] if paths else "", transcript.value)
                    if result:
                        ui.notify("Voz salva.", type="positive")
                        cards.refresh()

                ui.button("Salvar voz", icon="add", on_click=save).props("no-caps")

            @ui.refreshable
            def cards():
                voices = service.read()["voices"]
                if not voices:
                    ui.label("Nenhuma voz salva ainda. Adicione a primeira acima.").classes("studio-muted")
                for voice in voices:
                    with ui.card().classes("studio-card w-full"):
                        ui.label(voice["name"]).classes("text-lg font-semibold")
                        ui.label(voice["transcript"] or "Transcrição automática ao gerar.").classes("studio-muted")
                        ui.audio(voice["reference"]).classes("w-full")
                        ui.button("Criar áudio", on_click=lambda: ui.navigate.to("/")).props("flat no-caps")

            cards()

    @ui.page("/training")
    def training_page():
        uploaded = []
        state = {"project": "", "selected": "", "test": None}
        with shell("Treinar uma voz"):
            ui.label("Sua voz. Um passo de cada vez.").classes("studio-title")
            ui.label(
                "Envie áudios prontos, revise os textos e ajuste uma cópia do modelo. Nada é treinado automaticamente."
            ).classes("studio-muted")
            with ui.stepper().props("vertical color=primary").classes("w-full") as stepper:
                with ui.step("project", title="1. Criar ou abrir um projeto"):
                    name = ui.input("Nome do projeto", value="minha_voz").props("outlined")
                    available = service.projects()
                    if available:
                        ui.select(
                            available, label="Ou continue um projeto salvo", on_change=lambda e: name.set_value(e.value)
                        ).props("outlined")
                    project_status = ui.label("Use um nome sem espaços ou acentos.")

                    async def open_project():
                        project = await action(service.project, name.value)
                        if project:
                            state["project"] = project.name
                            project_status.set_text("Projeto aberto: " + project.name)
                            rows.refresh()
                            stepper.next()

                    ui.button("Abrir e continuar", icon="arrow_forward", on_click=open_project).props("no-caps")
                with ui.step("upload", title="2. Enviar seus áudios prontos"):
                    ui.markdown(
                        "Selecione **um ou vários arquivos** da mesma pessoa. O aplicativo gera os textos e divide gravações longas. Depois você confere tudo. Não é preciso criar planilhas."
                    )
                    ui.label(
                        "Prefira gravações sem música ou ruído. O primeiro uso baixa o modelo de transcrição."
                    ).classes("studio-muted")
                    upload_status = ui.label("Nenhum arquivo enviado.")
                    audio_upload(uploaded, upload_status, multiple=True)
                    language = ui.select(
                        {"pt": "Português", "en": "Inglês", "es": "Espanhol", "": "Detectar idioma"},
                        value="pt",
                        label="Idioma das gravações",
                    ).props("outlined")
                    consent = ui.checkbox("Tenho autorização para usar estas gravações no treinamento")
                    import_status = ui.label("").classes("whitespace-pre-wrap")

                    async def import_audio():
                        import_button.disable()
                        import_status.set_text("Importando e transcrevendo… O processamento pode levar alguns minutos.")
                        try:
                            result = await action(
                                service.import_audio, state["project"], list(uploaded), language.value, consent.value
                            )
                            if result is not None:
                                import_status.set_text(result)
                                uploaded.clear()
                                rows.refresh()
                        finally:
                            import_button.enable()

                    import_button = ui.button("Importar e gerar os textos", icon="upload", on_click=import_audio).props(
                        "no-caps"
                    )
                    ui.button("Revisar exemplos", icon="arrow_forward", on_click=stepper.next).props("flat no-caps")
                with ui.step("review", title="3. Ouvir, corrigir e aprovar"):
                    ui.label(
                        "Selecione uma linha, ouça e confira se o texto corresponde à fala. Somente exemplos aprovados entram no treino."
                    ).classes("studio-muted")
                    sample_audio = ui.audio("").classes("w-full")
                    correction = ui.textarea("Texto correto").props("outlined rows=3")
                    approved = ui.checkbox("Ouvi este trecho e aprovo seu uso")

                    def select_sample(event):
                        selected = event.selection
                        if not selected:
                            state["selected"] = ""
                            return
                        row = selected[0]
                        state["selected"] = row["id"]
                        correction.set_value(row["text"])
                        approved.set_value(row["approved"])
                        p = service.project(state["project"])
                        sample_audio.set_source(str(p.path / "wavs" / (row["id"] + ".wav")))

                    @ui.refreshable
                    def rows():
                        if not state["project"]:
                            ui.label("Abra um projeto para ver os exemplos.")
                            return
                        p = service.project(state["project"])
                        ui.markdown(p.summary())
                        items = [
                            {"id": row[0], "approved": row[1], "text": row[2], "seconds": row[3]} for row in p.rows()
                        ]
                        ui.table(
                            columns=[
                                {"name": "text", "label": "Texto reconhecido", "field": "text", "align": "left"},
                                {"name": "approved", "label": "Aprovado", "field": "approved"},
                                {"name": "seconds", "label": "Segundos", "field": "seconds"},
                            ],
                            rows=items,
                            row_key="id",
                            selection="single",
                            on_select=select_sample,
                            pagination=8,
                        ).classes("w-full")

                    rows()

                    async def save_review():
                        try:
                            await run.io_bound(
                                service.review, state["project"], state["selected"], correction.value, approved.value
                            )
                            ui.notify("Revisão salva.", type="positive")
                            rows.refresh()
                        except Exception as error:
                            ui.notify(str(error), type="negative")

                    ui.button("Salvar revisão deste trecho", icon="check", on_click=save_review).props("no-caps")
                    prepare_status = ui.label("").classes("whitespace-pre-wrap")

                    async def prepare():
                        result = await action(service.prepare, state["project"])
                        if result:
                            prepare_status.set_text("Exemplos preparados.")
                            stepper.next()

                    ui.label(
                        "Mínimo para testar o fluxo: 3 trechos e 15 segundos aprovados. Isso não é suficiente para garantir uma boa voz."
                    ).classes("text-sm studio-muted")
                    ui.button("Preparar e continuar", icon="arrow_forward", on_click=prepare).props("no-caps")
                with ui.step("train", title="4. Treinar e acompanhar"):
                    ui.markdown(
                        "Requer **GPU NVIDIA/CUDA**. O modelo original é preservado. Novos treinos do mesmo projeto retomam seus checkpoints; crie outro projeto para um experimento independente."
                    )
                    epochs = ui.select(
                        {
                            5: "Teste rápido · 5 passagens",
                            20: "Ajuste inicial · 20 passagens",
                            50: "Ajuste prolongado · 50 passagens",
                        },
                        value=5,
                        label="Perfil de treinamento",
                    ).props("outlined")
                    ui.label("Mais passagens não garantem melhoria. Comece pelo teste rápido.").classes("studio-muted")

                    async def start():
                        try:
                            await run.io_bound(service.start_training, state["project"], epochs.value)
                            ui.notify("Treinamento iniciado.", type="positive")
                        except Exception as error:
                            ui.notify(str(error), type="negative")

                    with ui.row():
                        ui.button("Iniciar treinamento", icon="play_arrow", on_click=start).props("no-caps")
                        ui.button("Parar", icon="stop", on_click=lambda: action(service.stop_training)).props(
                            "outline no-caps"
                        )
                    job = ui.label(service.job["message"]).classes(
                        "whitespace-pre-wrap w-full bg-slate-100 p-4 rounded-lg text-sm"
                    )
                    ui.timer(1, lambda: job.set_text(service.job["message"]))
                    ui.button("Testar uma versão", icon="arrow_forward", on_click=stepper.next).props("flat no-caps")
                with ui.step("test", title="5. Ouvir o resultado e avaliar"):
                    checkpoint = ui.select({}, label="Versão treinada (checkpoint)").props("outlined")

                    async def refresh_checkpoints():
                        result = await action(service.checkpoints, state["project"])
                        if result is not None:
                            checkpoint.set_options({p: Path(p).name for p in result})
                            if not result:
                                ui.notify("Nenhum checkpoint ainda. Aguarde o treino salvar uma versão.", type="info")

                    ui.button("Buscar versões", icon="refresh", on_click=refresh_checkpoints).props("flat no-caps")
                    ref_paths = []
                    ref_status = ui.label("Envie uma referência curta para comparar as versões.")
                    audio_upload(ref_paths, ref_status)
                    ref_text = ui.textarea("Texto da referência (opcional)").props("outlined rows=2")
                    test_text = ui.textarea("Frase nova para testar", value="Olá! Este é um teste da minha voz.").props(
                        "outlined rows=2"
                    )
                    result_audio = ui.audio("").classes("w-full")

                    async def test_voice():
                        result = await action(
                            service.generate,
                            test_text.value,
                            ref_paths[-1] if ref_paths else None,
                            ref_text.value,
                            None,
                            state["project"],
                            checkpoint.value,
                        )
                        if result:
                            state["test"] = result
                            result_audio.set_source(result["path"])

                    ui.button("Gerar teste desta versão", icon="play_arrow", on_click=test_voice).props("no-caps")
                    rating = ui.radio(["Ficou bom", "Precisa melhorar"])
                    note = ui.textarea("O que precisa melhorar?").props("outlined rows=2")

                    async def rate():
                        if not state["test"]:
                            ui.notify("Gere um teste primeiro.", type="warning")
                            return
                        try:
                            await run.io_bound(service.rate, state["test"]["id"], rating.value, note.value)
                            ui.notify("Avaliação salva no histórico.", type="positive")
                        except Exception as error:
                            ui.notify(str(error), type="negative")

                    ui.button("Guardar avaliação", icon="save", on_click=rate).props("no-caps")
                    ui.label(
                        "Avaliações não alteram os pesos. Para ensinar uma correção, envie um áudio correto, revise e treine novamente."
                    ).classes("studio-muted")

    @ui.page("/history")
    def history_page():
        with shell("Histórico"):
            ui.label("Tudo o que você criou.").classes("studio-title")
            entries = service.read()["history"]
            if not entries:
                with ui.card().classes("studio-card w-full"):
                    ui.icon("graphic_eq", size="40px", color="primary")
                    ui.label("Seu primeiro áudio vai aparecer aqui.")
                    ui.button("Criar áudio", on_click=lambda: ui.navigate.to("/")).props("no-caps")
            for entry in entries:
                with ui.card().classes("studio-card w-full"):
                    ui.label(entry["text"]).classes("font-semibold")
                    ui.label(entry["created_at"][:19].replace("T", " ") + " UTC").classes("text-xs studio-muted")
                    if entry["checkpoint"]:
                        ui.label("Modelo: " + Path(entry["checkpoint"]).name).classes("text-sm studio-muted")
                    ui.audio(entry["path"]).classes("w-full")
                    ui.button(
                        "Baixar WAV", icon="download", on_click=lambda path=entry["path"]: ui.download.file(path)
                    ).props("flat no-caps")
                    if entry["rating"]:
                        ui.badge(entry["rating"])
                        ui.label(entry["note"])

    @ui.page("/settings")
    def settings_page():
        with shell("Configurações"):
            ui.label("Controle o seu estúdio.").classes("studio-title")
            hardware, settings = service.hardware(), service.read()["settings"]
            with ui.card().classes("studio-card w-full"):
                ui.label("Seu equipamento").classes("font-semibold text-lg")
                ui.label(
                    hardware["name"]
                    + (f" · {hardware['memory']} GB de memória" if hardware["cuda"] else " · CUDA indisponível")
                )
                ui.label(
                    "CUDA acelera o cálculo. TensorRT é uma otimização de execução e não está integrado a este estúdio."
                ).classes("studio-muted")
            with ui.expansion("Dicionário de pronúncia", icon="spellcheck").classes("w-full"):
                ui.label(
                    "Uma regra por linha. A substituição vale para o texto gerado, mantendo o roteiro original salvo."
                ).classes("studio-muted")
                pronunciation = ui.textarea(
                    "Palavra = como ler",
                    value="\n".join(
                        f"{key} = {value}" for key, value in service.read().get("pronunciations", {}).items()
                    ),
                ).props("outlined rows=5")

                async def save_pronunciations():
                    try:
                        await run.io_bound(service.set_pronunciations, pronunciation.value)
                        ui.notify("Dicionário salvo. Gere novamente os trechos afetados.", type="positive")
                    except Exception as error:
                        ui.notify(str(error), type="negative")

                ui.button("Salvar pronúncias", on_click=save_pronunciations).props("no-caps")
            with ui.card().classes("studio-card w-full"):
                ui.label("Downloads de transcrição e tradução").classes("font-semibold text-lg")
                cache_only = ui.switch(
                    "Usar somente modelos já baixados nessas duas ferramentas",
                    value=service.read().get("offline", False),
                )
                ui.label(
                    "Ative depois de baixar os modelos. Geração e treinamento também precisam de seus pesos no cache para uso sem internet."
                ).classes("studio-muted text-sm")
                ui.button(
                    "Salvar preferência de cache", on_click=lambda: action(service.set_offline, cache_only.value)
                ).props("flat no-caps")
            with ui.card().classes("studio-card w-full"):
                device = ui.select(
                    {"auto": "Automático", "cuda": "NVIDIA / CUDA", "cpu": "CPU"},
                    value=settings["device"],
                    label="Onde gerar o áudio",
                ).props("outlined")
                steps = ui.select(
                    {16: "Rápido · 16 etapas", 32: "Equilibrado · 32 etapas", 48: "Mais etapas · 48"},
                    value=settings["steps"],
                    label="Etapas de geração",
                ).props("outlined")
                ui.label(
                    "Mais etapas usam mais tempo e podem mudar o resultado; compare os áudios para escolher."
                ).classes("studio-muted")
                speed = ui.number("Velocidade (1 = padrão)", value=settings["speed"], min=0.3, max=2, step=0.1).props(
                    "outlined"
                )

                async def save():
                    try:
                        await run.io_bound(service.settings, device.value, steps.value, speed.value)
                        ui.notify("Configurações salvas.", type="positive")
                    except Exception as error:
                        ui.notify(str(error), type="negative")

                ui.button("Salvar configurações", icon="save", on_click=save).props("no-caps")
                ui.label(
                    "Para melhorar a naturalidade, priorize referência limpa, textos corretos e ajuste fino revisado. Nenhuma opção garante qualidade superior."
                ).classes("studio-muted")


def main():
    parser = argparse.ArgumentParser(description="F5-TTS Studio — interface local em português")
    parser.add_argument("--port", type=int, default=7862)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--workspace", type=Path, default=None, help="Pasta para vozes, referências e histórico")
    args = parser.parse_args()
    register_pages(StudioService(home=args.workspace))
    ui.run(
        host="127.0.0.1",
        port=args.port,
        title="F5 Studio · Estúdio de voz",
        reload=False,
        show=not args.no_browser,
        language="pt-BR",
        favicon="🎙️",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
