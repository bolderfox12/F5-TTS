"""Browser-side microphone monitoring and synchronized ADR recording."""

from nicegui.element import Element


class ADRRecorder(Element, component="adr_recorder.js"):
    def __init__(self, t, on_busy, on_saved):
        super().__init__()
        self._props["labels"] = {
            "mic": t("Microfone"),
            "test": t("Ativar e testar microfone"),
            "off": t("Desligar microfone"),
            "level": t("Nível do microfone"),
            "guide": t("Áudio guia"),
            "mute": t("Mudo"),
            "original": t("Áudio original baixo (20%)"),
            "background": t("Apenas música e efeitos (M&E)"),
            "guideMissing": t("Prepare o guia M&E antes de gravar com essa opção."),
            "denied": t("Acesso ao microfone negado. Permita o microfone nas configurações deste site no navegador."),
            "missing": t("Nenhum microfone encontrado. Conecte um microfone e tente novamente."),
            "unsupported": t("Use um navegador atualizado e abra o Studio em localhost ou 127.0.0.1."),
            "failed": t("Não foi possível concluir a gravação. Tente novamente."),
            "countdown": t("Contagem para gravar"),
            "recording": t("Gravando:"),
            "cancel": t("Cancelar gravação"),
            "saving": t("Salvando take…"),
            "ready": t("Take gravado."),
            "player": t("Aguarde o vídeo ficar disponível no player."),
            "hint": t("Use fones de ouvido para evitar que o áudio guia entre na gravação."),
        }
        self._props.update(video=None, background="")
        self.on("busy", on_busy)
        self.on("saved", on_saved)

    def set_video(self, video):
        self._props.update(video=video.id if video else None, background="")
        self.run_method("clearStatus")

    def set_background(self, url):
        self._props["background"] = url
