"""Controls for recording, reviewing and choosing ADR takes."""

from nicegui import ui


def take_panel(t, project, segment_id, row, recorder, take_offsets, action, refresh, busy, *, save):
    active = project.active_takes.get(segment_id)
    recordings = project.takes.get(segment_id, {})

    def record():
        start, end = row["start"], row["end"]
        if start is None or end is None or not 0 <= start < end <= project.duration or end - start > 180:
            return ui.notify(t("Ajuste o início e o fim da fala para gravar até 180 segundos."), type="warning")
        recorder.run_method("record", start, end, f"/api/dubbing/{project.id}/segments/{segment_id}/takes")

    async def choose(take_id=None):
        await action(project.choose_take, segment_id, take_id)
        save()
        refresh()

    async def delete(take_id):
        await action(project.forget_take, segment_id, take_id)
        save()
        refresh()

    def adjust_offset(event):
        take_offsets[segment_id] = int(event.value or 0)
        save()

    with ui.row().classes("w-full items-center"):
        ui.button(t("Gravar com contagem (3-2-1)"), icon="mic", on_click=record).props("no-caps").set_enabled(not busy)
        if active:
            ui.button(t("Usar voz de IA nesta fala"), on_click=choose).props("flat no-caps").set_enabled(not busy)
    if recordings:
        ui.label(t("Ajuste fino desta fala (ms)"))
        ui.slider(min=-300, max=300, step=1, value=take_offsets.get(segment_id, 0), on_change=adjust_offset).props(
            "label-always"
        ).classes("w-full max-w-sm").set_enabled(not busy)
    for take_id, take in recordings.items():
        with ui.row().classes("w-full items-center"):
            ui.label(t("Take {value_0}", value_0=take.number)).classes("font-medium")
            ui.audio(take.path).classes("w-full max-w-xs")
            if take_id == active:
                ui.badge(t("Take ativo"), color="teal-8")
            else:

                def use(identifier=take_id):
                    return choose(identifier)

                ui.button(t("Usar take"), icon="check", on_click=use).props("outline no-caps").set_enabled(not busy)

            def remove(identifier=take_id):
                return delete(identifier)

            ui.button(icon="delete_outline", on_click=remove).props("flat").tooltip(t("Excluir take")).set_enabled(
                not busy
            )
