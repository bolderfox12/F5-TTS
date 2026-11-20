"""Character-first story editor backed by the existing narration renderer."""

import re
from uuid import uuid4

from nicegui import ui

from f5_tts.studio import chatterbox_backend as chatterbox


def register_story_pages(service, shell, action):
    t = service.translate_ui

    @ui.page("/stories")
    def story_page():
        voices = {voice["id"]: voice["name"] for voice in service.read()["voices"]}
        models = service.model_choices()
        state = {"project": None, "selected": None, "mix": None, "cast": []}

        def default_cast():
            available = list(voices)
            return [
                {
                    "id": uuid4().hex,
                    "name": name,
                    "voice": available[index % len(available)] if available else None,
                    "model_id": "original",
                }
                for index, name in enumerate([t("Narrador"), "Ana", "Bruno"])
            ]

        state["cast"] = default_cast()
        with shell("Histórias e personagens"):
            ui.label(t("Uma história. Vários personagens e vozes.")).classes("studio-title")
            ui.label(
                t("Monte seu elenco, escreva as falas e gere o áudio completo para sua novelinha, audiobook ou vídeo.")
            ).classes("studio-muted")
            with ui.card().classes("studio-card w-full gap-4"):
                saved = ui.select({p["id"]: p["name"] for p in service.stories()}, label=t("Abrir uma história")).props(
                    "outlined"
                )

                async def open_story():
                    project = await action(service.narration, saved.value)
                    if project and project.get("kind") == "story":
                        state.update(project=project["id"], cast=project["characters"], selected=None)
                        name.set_value(project["name"])
                        script.set_value(service.story_script(project))
                        cast.refresh()
                        refresh_project()

                def new_story():
                    state.update(project=None, selected=None, mix=None, cast=default_cast())
                    name.set_value("")
                    script.set_value("")
                    saved.set_value(None)
                    cast.refresh()
                    turns.refresh()
                    output.set_source("")
                    downloads.refresh()

                with ui.row():
                    ui.button(t("Abrir história"), icon="folder_open", on_click=open_story).props("no-caps")
                    ui.button(t("Nova história"), icon="add", on_click=new_story).props("flat no-caps")
                name = ui.input(
                    t("Nome da história"), placeholder=t("Minha série, episódio ou história para YouTube")
                ).props("outlined")

            with ui.card().classes("studio-card w-full gap-4"):
                ui.label(t("01 / Elenco e vozes")).classes("text-lg font-semibold")
                ui.label(
                    t("Salve as referências em Minhas vozes. Escolha uma voz e um modelo para cada personagem.")
                ).classes("studio-muted")
                ui.link(t("Abrir Minhas vozes"), "/voices")

                @ui.refreshable
                def cast():
                    for character in state["cast"]:
                        with ui.row().classes("w-full items-start gap-3 flex-wrap"):

                            def rename(event, item=character):
                                previous = item.get("_script_name", item["name"])
                                item["name"] = event.value
                                if previous and event.value:
                                    script.set_value(
                                        re.sub(
                                            r"(?im)^" + re.escape(previous) + r"\s*:",
                                            lambda _match: event.value + ":",
                                            script.value or "",
                                        )
                                    )
                                    item["_script_name"] = event.value

                            ui.input(t("Personagem"), value=character["name"], on_change=rename).props(
                                "outlined"
                            ).style("flex:1 1 180px;width:auto")
                            ui.select(
                                voices,
                                label=t("Voz do personagem"),
                                value=character["voice"],
                                on_change=lambda event, item=character: item.update(voice=event.value),
                            ).props("outlined").style("flex:1 1 180px;width:auto")
                            model = (
                                ui.select(models, label=t("Modelo do personagem"), value=character["model_id"])
                                .props("outlined")
                                .style("flex:1 1 180px;width:auto")
                            )
                            language = (
                                ui.select(
                                    chatterbox.LANGUAGES,
                                    label=t("Idioma do Chatterbox"),
                                    value=character.get("language", chatterbox.DEFAULT_LANGUAGE),
                                    on_change=lambda event, item=character: item.update(language=event.value),
                                )
                                .props("outlined")
                                .style("flex:1 1 180px;width:auto")
                            )
                            language.set_visibility(model.value == chatterbox.MODEL_ID)

                            def change_model(event, item=character, field=language):
                                item["model_id"] = event.value
                                field.set_visibility(event.value == chatterbox.MODEL_ID)

                            model.on_value_change(change_model)

                            def remove(item=character):
                                state["cast"].remove(item)
                                cast.refresh()

                            ui.button(icon="person_remove", on_click=remove).props("flat round").tooltip(
                                t("Remover personagem")
                            )

                cast()

                def add_character():
                    state["cast"].append(
                        {"id": uuid4().hex, "name": "", "voice": next(iter(voices), None), "model_id": "original"}
                    )
                    cast.refresh()

                ui.button(t("Adicionar personagem"), icon="person_add", on_click=add_character).props("no-caps outline")

            with ui.card().classes("studio-card w-full gap-4"):
                ui.label(t("02 / Roteiro da história")).classes("text-lg font-semibold")
                ui.label(
                    t(
                        "Comece cada fala com o nome do personagem e dois-pontos. Linhas seguintes continuam a mesma fala. O primeiro personagem é o narrador das linhas iniciais sem nome."
                    )
                ).classes("studio-muted")
                template = ui.select(
                    {
                        "dialogue": t("História com diálogos"),
                        "youtube": t("História para YouTube"),
                        "drama": t("Novela curta"),
                    },
                    value="dialogue",
                    label=t("Exemplo de roteiro"),
                ).props("outlined")

                def load_example():
                    names = [character["name"] for character in state["cast"] if character["name"]]
                    if len(names) < 3:
                        ui.notify(t("Adicione um narrador e dois personagens para usar o exemplo."), type="warning")
                        return
                    examples = {
                        "dialogue": t(
                            "{narrator}: Era uma manhã tranquila, até que alguém bateu à porta.\n\n{first}: Você também ouviu esse barulho?\n\n{second}: Ouvi. Vamos descobrir juntos.\n\n{narrator}: E foi assim que a aventura começou."
                        ),
                        "youtube": t(
                            "{narrator}: Hoje você vai conhecer uma história que começou com uma escolha inesperada.\n\n{first}: E se tentássemos um caminho diferente?\n\n{second}: Podemos tentar. Mas precisamos de um plano.\n\n{narrator}: O que aconteceu depois mudou tudo. Conte nos comentários o que você faria."
                        ),
                        "drama": t(
                            "{narrator}: Naquele reencontro, ninguém esperava ouvir a verdade.\n\n{first}: Eu guardei esta carta durante todos esses anos.\n\n{second}: Então você sabia de tudo?\n\n{narrator}: O silêncio foi interrompido por uma nova batida à porta. Continua no próximo episódio."
                        ),
                    }
                    script.set_value(
                        examples[template.value].format(narrator=names[0], first=names[1], second=names[2])
                    )

                ui.button(t("Usar exemplo de roteiro"), icon="auto_stories", on_click=load_example).props(
                    "flat no-caps"
                )
                script = ui.textarea(
                    t("Roteiro com personagens"),
                    placeholder=t("Narrador: A história começa aqui.\nAna: Olá!\nBruno: Vamos começar?"),
                ).props("outlined rows=12")

                async def save_story():
                    project = await action(
                        service.save_story, state["project"], name.value, script.value, state["cast"]
                    )
                    if project:
                        state.update(project=project["id"], cast=project["characters"])
                        saved.set_options({p["id"]: p["name"] for p in service.stories()})
                        saved.set_value(project["id"])
                        cast.refresh()
                        refresh_project()
                        ui.notify(t("História e elenco salvos."), type="positive")

                ui.button(t("Salvar história e distribuir falas"), icon="save", on_click=save_story).props("no-caps")

            with ui.card().classes("studio-card w-full gap-4"):
                ui.label(t("03 / Falas e direção")).classes("text-lg font-semibold")

                @ui.refreshable
                def turns():
                    if not state["project"]:
                        ui.label(t("Salve a história para editar, reordenar e ouvir as falas."))
                        return
                    project = service.narration(state["project"])
                    names = {character["id"]: character["name"] for character in project["characters"]}

                    def select(event):
                        if not event.selection:
                            return
                        block = next(b for b in project["blocks"] if b["id"] == event.selection[0]["id"])
                        state["selected"] = block["id"]
                        dialogue.set_value(block["text"])
                        speaker.set_options(names)
                        speaker.set_value(block["character"])
                        pause.set_value(block["pause"])
                        clip.set_source(block["render"]["path"] if service.block_ready(block) else "")

                    ui.table(
                        columns=[
                            {"name": "order", "label": t("Ordem"), "field": "order"},
                            {"name": "character", "label": t("Personagem"), "field": "character"},
                            {"name": "text", "label": t("Fala"), "field": "text", "align": "left"},
                            {"name": "status", "label": t("Áudio"), "field": "status"},
                        ],
                        rows=[
                            {
                                "id": b["id"],
                                "order": i + 1,
                                "character": names[b["character"]],
                                "text": b["text"][:120],
                                "status": t("Pronto") if service.block_ready(b) else t("Gerar/atualizar"),
                            }
                            for i, b in enumerate(project["blocks"])
                        ],
                        row_key="id",
                        selection="single",
                        on_select=select,
                        pagination=10,
                    ).classes("w-full")

                turns()
                dialogue = ui.textarea(t("Texto da fala selecionada")).props("outlined rows=4")
                speaker = ui.select({}, label=t("Personagem desta fala")).props("outlined")
                pause = ui.number(t("Pausa depois da fala (segundos)"), value=0.4, min=0, max=10, step=0.1).props(
                    "outlined"
                )
                clip = ui.audio("").classes("w-full")

                async def edit_turn():
                    project = await action(
                        service.edit_story_block,
                        state["project"],
                        state["selected"],
                        dialogue.value,
                        speaker.value,
                        pause.value,
                    )
                    if project:
                        script.set_value(service.story_script(project))
                        refresh_project()

                async def render_turn():
                    entry = await action(service.render_block, state["project"], state["selected"])
                    if entry:
                        clip.set_source(entry["path"])
                        refresh_project()

                async def move(direction):
                    project = await action(service.move_block, state["project"], state["selected"], direction)
                    if project:
                        script.set_value(service.story_script(project))
                        refresh_project()

                async def delete_turn():
                    project = await action(service.delete_story_block, state["project"], state["selected"])
                    if project:
                        state["selected"] = None
                        script.set_value(service.story_script(project))
                        refresh_project()

                with ui.row().classes("flex-wrap"):
                    ui.button(t("Salvar fala"), icon="save", on_click=edit_turn).props("no-caps")
                    ui.button(t("Gerar esta fala"), icon="play_arrow", on_click=render_turn).props("outline no-caps")
                    ui.button(icon="arrow_upward", on_click=lambda: move(-1)).props("flat").tooltip(
                        t("Mover para cima")
                    )
                    ui.button(icon="arrow_downward", on_click=lambda: move(1)).props("flat").tooltip(
                        t("Mover para baixo")
                    )
                    ui.button(t("Excluir fala"), icon="delete", on_click=delete_turn).props("flat no-caps")

            with ui.card().classes("studio-card w-full gap-4"):
                ui.label(t("04 / Áudio completo da história")).classes("text-lg font-semibold")
                ui.label(
                    t(
                        "Salve o roteiro antes de gerar. Somente falas novas ou alteradas precisam ser geradas novamente."
                    )
                ).classes("studio-muted")
                progress = ui.label("")
                ui.timer(1, lambda: progress.set_text(t(service.render_status)))
                output = ui.audio("").classes("w-full")

                async def render_story():
                    generate.disable()
                    try:
                        result = await action(service.render_narration, state["project"])
                        if result:
                            refresh_project()
                    finally:
                        generate.enable()

                generate = ui.button(t("Gerar história completa"), icon="movie", on_click=render_story).props("no-caps")
                ui.button(
                    t("Parar depois da fala atual"), icon="stop", on_click=lambda: action(service.stop_render)
                ).props("flat no-caps")

                @ui.refreshable
                def downloads():
                    if state["mix"]:
                        ui.button(
                            t("Baixar história WAV"),
                            icon="download",
                            on_click=lambda: ui.download.file(state["mix"]["path"]),
                        ).props("flat no-caps")
                        ui.button(
                            t("Baixar legendas SRT"),
                            icon="subtitles",
                            on_click=lambda: ui.download.file(state["mix"]["srt"]),
                        ).props("flat no-caps")
                    if state["project"]:
                        ui.button(
                            t("Baixar roteiro TXT"),
                            icon="description",
                            on_click=lambda: ui.download.content(
                                service.story_script(service.narration(state["project"])), "story.txt"
                            ),
                        ).props("flat no-caps")

                downloads()

            def refresh_project():
                project = service.narration(state["project"])
                state["mix"] = (
                    project.get("mix")
                    if project["blocks"] and all(service.block_ready(b) for b in project["blocks"])
                    else None
                )
                turns.refresh()
                downloads.refresh()
                output.set_source(state["mix"]["path"] if state["mix"] else "")
