"""Browser microphone control; captured audio is sent only after confirmation."""

from nicegui.element import Element


class Microphone(Element, component="microphone.js"):
    def __init__(self, on_recorded, translator=None, *, for_chat=False, for_command=False):
        super().__init__()
        t = translator or (lambda text: text)
        self._props["labels"] = {
            "start": t("Gravar pelo microfone"),
            "stop": t("Parar gravação"),
            "use": t("Usar esta gravação"),
            "recording": t("Gravando:"),
            "hint": t("Fale de 1 a 30 segundos. Prefira até 12 segundos, em um ambiente sem música."),
            "permission": t("Permita o uso do microfone no navegador."),
            "unsupported": t("Use um navegador atualizado e abra o Studio em localhost ou 127.0.0.1."),
            "failed": t("Não foi possível concluir a gravação. Tente novamente."),
            "preview": t("Ouça e clique em “Usar esta gravação”, ou grave novamente."),
            "active": t("Microfone ligado."),
            "denied": t("Acesso ao microfone negado. Permita o microfone nas configurações deste site no navegador."),
            "missing": t("Nenhum microfone encontrado. Conecte um microfone e tente novamente."),
            "busy": t("O microfone está indisponível ou em uso por outro programa."),
            "open_failed": t("Não foi possível abrir o microfone."),
            "too_large": t("Grave um trecho mais curto para usar como referência."),
            "preparing": t("Preparando referência…"),
            "selected": t("Gravação selecionada como referência."),
            "retry": t("Não foi possível usar a gravação. Grave de 1 a 30 segundos e tente novamente."),
        }
        if for_chat:
            self._props["stopSpeech"] = True
            self._props["labels"].update(
                start=t("Gravar pergunta"),
                use=t("Enviar pergunta por voz"),
                hint=t("Grave até 30 segundos. O Studio envia ao provedor apenas a transcrição da pergunta."),
                preview=t("Ouça e envie a pergunta ou grave novamente."),
                too_large=t("Grave uma pergunta mais curta."),
                preparing=t("Transcrevendo sua pergunta…"),
                selected=t("Pergunta enviada ao chat."),
                retry=t("Não foi possível transcrever a pergunta. Grave novamente."),
            )
        if for_command:
            self._props["stopSpeech"] = True
            self._props["labels"].update(
                start=t("Gravar comando"),
                use=t("Transcrever comando"),
                hint=t("Fale por até 30 segundos e revise o texto antes de enviar."),
                preparing=t("Transcrevendo comando…"),
                selected=t("Comando transcrito."),
            )
        self.on("recorded", on_recorded)
