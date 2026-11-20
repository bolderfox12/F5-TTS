"""Creative consoles backed by the Studio's saved review credentials."""

import base64
import json
import math
import re
from dataclasses import dataclass
from pathlib import PureWindowsPath
from time import monotonic
from uuid import uuid4

import httpx
from nicegui import run, ui
from nicegui.element import Element

from f5_tts.studio.microphone import Microphone
from f5_tts.studio.script_review import preferred_review_model


GOOGLE = "https://generativelanguage.googleapis.com/v1beta"
OPENAI = "https://api.openai.com/v1"
MEDIA_MODELS = {
    "gemini": {
        "image": ["gemini-nano-banana-2.1", "gemini-3.1-flash-image"],
        "video": ["gemini-omni-1.2-flash"],
    },
    "openai": {"image": ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst"]},
}
TEXT_MODELS = {
    "gemini": ["gemini-3.5-flash-lite", "gemini-3.5-flash"],
    "openai": ["gpt-5.6-sol", "gpt-4.1-mini"],
}
MEDIA_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4"}
USAGE_URLS = {"gemini": "https://aistudio.google.com/rate-limit", "openai": "https://platform.openai.com/usage"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
AUDIO_TYPES = {".mp3": "audio/mp3", ".wav": "audio/wav", ".m4a": "audio/m4a", ".flac": "audio/flac"}
VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
DOCUMENT_TYPES = {".pdf": "application/pdf", ".txt": "text/plain"}
OPENAI_DOCUMENT_TYPES = {
    **DOCUMENT_TYPES,
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ATTACHMENT_LIMIT = 12_000_000
MAX_ATTACHMENTS = 6


@dataclass(frozen=True)
class Attachment:
    name: str
    mime: str
    data: bytes

    def encoded(self):
        return base64.b64encode(self.data).decode("ascii")


class SpokenReply(Element, component="spoken_reply.js"):
    """Read chat replies using voices available to the browser."""


def _attachment_types(provider, kind):
    if kind == "video":
        return {**IMAGE_TYPES, **VIDEO_TYPES}
    if kind == "image":
        return IMAGE_TYPES
    if provider == "gemini":
        return {**IMAGE_TYPES, **AUDIO_TYPES, **VIDEO_TYPES, **DOCUMENT_TYPES}
    return {**IMAGE_TYPES, **OPENAI_DOCUMENT_TYPES}


def _rate_limit_message(response, t):
    try:
        error = response.json()["error"]
        if not isinstance(error, dict):
            error = {}
    except (ValueError, KeyError, TypeError):
        error = {}

    quota_codes = (
        "insufficient_quota",
        "usage_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "billing_not_active",
        "credit_balance_exhausted",
    )
    if error.get("code") in quota_codes or error.get("type") in quota_codes:
        return t("A cota ou o crédito da API acabou. Confira o faturamento e os limites do projeto.")

    retry = response.headers.get("Retry-After", "")
    details = error.get("details")
    daily_limit = False
    for detail in details if isinstance(details, list) else ():
        if not isinstance(detail, dict):
            continue
        if detail.get("@type") == "type.googleapis.com/google.rpc.RetryInfo" and not retry:
            match = re.fullmatch(r"([0-9]{1,9}(?:\.[0-9]{1,9})?)s", str(detail.get("retryDelay")))
            if match:
                retry = str(math.ceil(float(match[1])))
        violations = detail.get("violations")
        if detail.get("@type") != "type.googleapis.com/google.rpc.QuotaFailure" or not isinstance(violations, list):
            continue
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            if str(violation.get("quotaValue")) == "0":
                return t("Este projeto tem cota zero para esse modelo. Confira o plano da API ou escolha outro modelo.")
            if "perday" in str(violation.get("quotaId", "")).casefold():
                daily_limit = True

    if daily_limit:
        return t("A cota diária deste modelo foi atingida. Confira quando ela é renovada no painel do provedor.")
    if re.fullmatch(r"[0-9]{1,9}", retry):
        return t("Limite temporário da API. Aguarde {seconds} s antes de tentar novamente.", seconds=max(1, int(retry)))
    if error.get("code") in ("rate_limit_exceeded", "slow_down"):
        return t("Limite temporário da API. Aguarde um pouco antes de tentar novamente.")
    return t("HTTP 429. Confira a cota e os limites do modelo no painel do provedor.")


def _text_request(provider, model, history, prompt, attachments):
    if provider == "gemini":
        contents = [
            {"role": "model" if role == "assistant" else role, "parts": [{"text": text}]} for role, text in history
        ]
        parts = [{"text": prompt}]
        for item in attachments:
            if item.mime == "text/plain":
                parts.append({"text": f"{item.name}\n{item.data.decode('utf-8', errors='replace')}"})
            else:
                parts.append({"inlineData": {"mimeType": item.mime, "data": item.encoded()}})
        return f"{GOOGLE}/models/{model}:streamGenerateContent?alt=sse", {
            "contents": [*contents, {"role": "user", "parts": parts}]
        }

    contents = [{"role": role, "content": text} for role, text in history]
    parts = [{"type": "input_text", "text": prompt}]
    for item in attachments:
        data = f"data:{item.mime};base64,{item.encoded()}"
        if item.mime.startswith("image/"):
            parts.append({"type": "input_image", "image_url": data, "detail": "auto"})
        else:
            parts.append({"type": "input_file", "filename": item.name, "file_data": data})
    return f"{OPENAI}/responses", {
        "model": model,
        "input": [*contents, {"role": "user", "content": parts}],
        "store": False,
        "stream": True,
    }


async def _stream_events(response):
    data = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif not line and data:
            event = "\n".join(data)
            data.clear()
            if event != "[DONE]":
                yield json.loads(event)
    if data and data != ["[DONE]"]:
        yield json.loads("\n".join(data))


def _provider_error(response, t):
    if response.status_code == 429:
        return _rate_limit_message(response, t)
    return t("O provedor retornou HTTP {code}. Confira a chave, o modelo e a cota.", code=response.status_code)


async def stream_console(service, provider, model, prompt, history, attachments):
    headers, model, prompt = _request_settings(service, provider, "text", model, prompt, attachments)
    url, body = await run.io_bound(_text_request, provider, model, history, prompt, attachments)
    complete = False
    try:
        async with httpx.AsyncClient(headers=headers, timeout=180) as client:
            async with client.stream("POST", url, json=body) as response:
                if response.is_error:
                    await response.aread()
                    response.raise_for_status()
                async for event in _stream_events(response):
                    if provider == "gemini":
                        if "error" in event:
                            break
                        for candidate in event.get("candidates", []):
                            for part in candidate.get("content", {}).get("parts", []):
                                if part.get("text") and not part.get("thought"):
                                    yield part["text"]
                            if candidate.get("finishReason") == "STOP":
                                complete = True
                    elif event.get("type") == "response.output_text.delta":
                        yield event["delta"]
                    elif event.get("type") == "response.completed":
                        complete = True
                    elif event.get("type") in ("error", "response.failed", "response.incomplete"):
                        break
    except httpx.HTTPStatusError as error:
        raise ValueError(_provider_error(error.response, service.translate_ui)) from None
    except httpx.RequestError:
        raise ValueError("A comunicação com o provedor de IA falhou.") from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("O provedor não retornou o conteúdo solicitado.") from None
    if not complete:
        raise ValueError("O provedor encerrou a resposta antes de concluir.")


def _media_result(client, provider, kind, model, prompt, attachments):
    if provider == "openai":
        fields = {"model": model, "prompt": prompt, "n": 1, "output_format": "png"}
        if attachments:
            images = [("image[]", (item.name, item.data, item.mime)) for item in attachments]
            response = client.post(f"{OPENAI}/images/edits", data=fields, files=images)
        else:
            response = client.post(f"{OPENAI}/images/generations", json=fields)
        return response.raise_for_status().json()["data"][0]["b64_json"], "image/png"
    response_format = {"type": kind}
    if kind == "video":
        response_format["duration"] = "8s"
    inputs = prompt
    if attachments:
        inputs = [
            {"type": item.mime.split("/")[0], "data": item.encoded(), "mime_type": item.mime} for item in attachments
        ]
        inputs.append({"type": "text", "text": prompt})
    response = client.post(
        f"{GOOGLE}/interactions", json={"model": model, "input": inputs, "response_format": response_format}
    )
    interaction = response.raise_for_status().json()
    for step in interaction["steps"]:
        if step["type"] == "model_output":
            for part in step["content"]:
                if part["type"] == kind and part.get("data"):
                    return part["data"], part["mime_type"]
    raise ValueError


def _request_settings(service, provider, kind, model, prompt, attachments):
    if provider not in MEDIA_MODELS or kind not in {"text", *MEDIA_MODELS[provider]}:
        raise ValueError("Este tipo de criação não está disponível neste console.")
    if service.read().get("offline", False):
        raise ValueError("Desative o modo offline para usar o console de IA.")
    key = service.review_api_key(provider)
    if not key:
        raise ValueError("Salve a chave da API deste provedor na revisão do roteiro.")
    model = (model or "").strip()
    prompt = (prompt or "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", model):
        raise ValueError("Informe um identificador de modelo válido.")
    if len(prompt) > 20000 or not (prompt or attachments):
        raise ValueError("Escreva um pedido de até 20.000 caracteres.")
    allowed_types = _attachment_types(provider, kind)
    if any(allowed_types.get(PureWindowsPath(item.name).suffix.lower()) != item.mime for item in attachments):
        raise ValueError("O tipo de arquivo não é aceito neste modo.")
    if len(attachments) > MAX_ATTACHMENTS or sum(len(item.data) for item in attachments) > ATTACHMENT_LIMIT:
        raise ValueError("Use até 6 anexos, com no máximo 12 MB no total.")
    headers = {"x-goog-api-key": key} if provider == "gemini" else {"Authorization": f"Bearer {key}"}
    return headers, model, prompt or "Descreva os anexos."


def generate_media(service, provider, kind, model, prompt, attachments):
    headers, model, prompt = _request_settings(service, provider, kind, model, prompt, attachments)
    try:
        with httpx.Client(headers=headers, timeout=600 if kind == "video" else 180) as client:
            encoded, mime = _media_result(client, provider, kind, model, prompt, attachments)
            if not mime.startswith(kind + "/") or mime not in MEDIA_SUFFIXES or len(encoded) > 140_000_000:
                raise ValueError
            data = base64.b64decode(encoded, validate=True)
            if not data:
                raise ValueError
    except httpx.HTTPStatusError as error:
        raise ValueError(_provider_error(error.response, service.translate_ui)) from None
    except httpx.RequestError:
        raise ValueError("A comunicação com o provedor de IA falhou.") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError("O provedor não retornou o conteúdo solicitado.") from None
    directory = service.home / "ai-media"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / f"{uuid4().hex}{MEDIA_SUFFIXES[mime]}"
    try:
        path.write_bytes(data)
    except OSError:
        path.unlink(missing_ok=True)
        raise ValueError("Não foi possível salvar o arquivo gerado.") from None
    return path


def creative_consoles(service, action, script_field, generate_audio):
    t = service.translate_ui
    speaker = SpokenReply()
    speaker.on(
        "unavailable",
        lambda: ui.notify(t("A voz do navegador está indisponível. Confira as vozes instaladas."), type="warning"),
    )

    def speak(text):
        speaker.run_method("speak", text, service.interface_language())

    def stop_speaking():
        speaker.run_method("stop")

    def provider_console(provider, name):
        history = []
        attachments = []
        selected_models = {}
        current_kind = "text"
        busy = False
        models = {
            "text": list(dict.fromkeys(filter(None, [preferred_review_model(provider), *TEXT_MODELS[provider]]))),
            **MEDIA_MODELS[provider],
        }

        def refresh():
            online = service.review_key_active(provider) and not service.read().get("offline", False)
            status.set_text(t("Online") if online else t("Offline"))
            status.classes(
                add="text-positive" if online else "studio-muted", remove="studio-muted" if online else "text-positive"
            )
            for control in (mode, model, prompt, uploader, submit, revise_button, reset_button, voice_mode):
                control.set_enabled(online and not busy)
            voice_mode.set_visibility(current_kind == "text")
            recorder.set_visibility(online and current_kind == "text" and voice_mode.value)
            unavailable.set_visibility(not online)

        def switch_mode():
            nonlocal current_kind
            selected_models[current_kind] = model.value
            current_kind = mode.value
            options = models[current_kind]
            selected = selected_models.get(current_kind) or options[0]
            model.set_options(list(dict.fromkeys([*options, selected])), value=selected)
            uploader.props(f'accept="{",".join(_attachment_types(provider, current_kind))}"')
            refresh()

        def toggle_voice(event):
            if not event.value:
                stop_speaking()
            refresh()

        async def receive_voice(event):
            nonlocal busy
            if busy or current_kind != "text" or not voice_mode.value:
                recorder.run_method("finish", False)
                return
            if not service.review_key_active(provider) or service.read().get("offline", False):
                recorder.run_method("finish", False)
                refresh()
                return
            busy = True
            refresh()
            try:
                transcript = await action(service.transcribe_chat_recording, event.args)
                recorder.run_method("finish", bool(transcript))
            finally:
                busy = False
                refresh()
            if transcript:
                await generate("text", transcript)

        @ui.refreshable
        def pending_files():
            for item in attachments:
                with ui.row().classes("items-center gap-1"):
                    ui.label(item.name).classes("text-sm break-all")
                    ui.button(icon="close", on_click=lambda file=item: remove_attachment(file)).props(
                        "flat dense round"
                    ).tooltip(t("Remover anexo"))

        def remove_attachment(item):
            if not busy and item in attachments:
                attachments.remove(item)
                pending_files.refresh()
                refresh()

        async def receive_attachments(event):
            try:
                if busy or not service.review_key_active(provider) or service.read().get("offline", False):
                    return
                allowed = _attachment_types(provider, mode.value)
                for file in event.files:
                    name = PureWindowsPath(file.name).name
                    mime = allowed.get(PureWindowsPath(name).suffix.lower())
                    if not mime:
                        ui.notify(t("O tipo de arquivo não é aceito neste modo."), type="warning")
                        continue
                    size = file.size()
                    if not 0 < size <= ATTACHMENT_LIMIT:
                        ui.notify(t("Use até 6 anexos, com no máximo 12 MB no total."), type="warning")
                        continue
                    data = await file.read()
                    if busy:
                        return
                    if (
                        len(attachments) >= MAX_ATTACHMENTS
                        or len(data) + sum(len(item.data) for item in attachments) > ATTACHMENT_LIMIT
                    ):
                        ui.notify(t("Use até 6 anexos, com no máximo 12 MB no total."), type="warning")
                        break
                    attachments.append(Attachment(name, mime, data))
                pending_files.refresh()
                refresh()
            finally:
                uploader.reset()

        def reset():
            stop_speaking()
            history.clear()
            attachments.clear()
            pending_files.refresh()
            results.clear()
            refresh()

        async def review_current_script():
            source = (script_field.value or "").strip()
            if not source:
                ui.notify(t("Escreva ou cole um roteiro no campo de narração primeiro."), type="warning")
                return
            instructions = (prompt.value or "").strip()
            request = t(
                "Revise o roteiro abaixo para narração. Preserve o idioma e os fatos e devolva apenas o roteiro completo."
            )
            if instructions:
                request += f"\n\n{instructions}"
            await generate("text", request + f"\n\n<roteiro>\n{source}\n</roteiro>", t("Revisar roteiro atual"))

        async def generate(kind=None, request=None, display=None):
            nonlocal busy
            if busy:
                return
            kind = kind or mode.value
            chosen_model = model.value if kind == mode.value else selected_models.get(kind) or models[kind][0]
            request = request if request is not None else (prompt.value or "").strip()
            files = tuple(attachments)
            if not (request or files):
                ui.notify(t("Escreva um pedido ou envie um anexo."), type="warning")
                return
            busy = True
            refresh()
            submit.props("loading")
            with results:
                with ui.column().classes("self-end max-w-[92%] bg-teal-50 rounded-lg px-3 py-2 gap-1"):
                    ui.label(t("Você")).classes("text-xs font-semibold")
                    if display or request:
                        ui.label(display or request).classes("whitespace-pre-wrap break-words w-full")
                    for item in files:
                        ui.label(item.name).classes("text-xs break-all")
                with ui.column().classes("w-full items-start gap-2 px-2 pb-3") as turn:
                    ui.label(name).classes("text-xs font-semibold")
                    reply = ui.label(t("Aguardando resposta…")).classes("whitespace-pre-wrap break-words w-full")
            scroll.scroll_to(percent=1)
            try:
                if kind == "text":
                    pieces = []
                    last_update = 0
                    try:
                        async for piece in stream_console(
                            service, provider, chosen_model, request, history[-8:], files
                        ):
                            pieces.append(piece)
                            now = monotonic()
                            if now - last_update >= 0.05:
                                reply.set_text("".join(pieces))
                                scroll.scroll_to(percent=1)
                                last_update = now
                        result = "".join(pieces).strip()
                        if not result:
                            raise ValueError("O provedor não retornou o conteúdo solicitado.")
                    except ValueError as error:
                        reply.set_text("".join(pieces) or t(str(error)))
                        if pieces:
                            with turn:
                                ui.label(t(str(error))).classes("text-negative text-sm")
                        return
                else:
                    result = await action(generate_media, service, provider, kind, chosen_model, request, files)
                    if result is None:
                        reply.set_text(t("A criação não foi concluída."))
                        return

                prompt.set_value("")
                attachments.clear()
                pending_files.refresh()
                with turn:
                    if kind == "text":
                        history.extend([("user", request or t("Descreva os anexos.")), ("assistant", result)])
                        del history[:-8]
                        reply.set_text(result)
                        with ui.row():
                            ui.button(
                                t("Usar como roteiro"), on_click=lambda value=result: script_field.set_value(value)
                            ).props("outline no-caps")
                            ui.button(
                                t("Baixar texto"),
                                on_click=lambda value=result: ui.download.content(value, "roteiro.txt"),
                            ).props("flat no-caps")
                            ui.button(
                                t("Gerar áudio com este texto"),
                                on_click=lambda value=result: generate_audio(value),
                            ).props("flat no-caps")
                            ui.button(
                                t("Ouvir resposta"), icon="volume_up", on_click=lambda value=result: speak(value)
                            ).props("flat no-caps")
                        if voice_mode.value:
                            speak(result)
                    else:
                        reply.delete()
                        if kind == "image":
                            ui.image(result).classes("w-full max-w-xl")
                        else:
                            ui.video(result).classes("w-full max-w-xl")
                        ui.button(t("Baixar arquivo"), on_click=lambda path=result: ui.download.file(path)).props(
                            "flat no-caps"
                        )
            finally:
                busy = False
                submit.props(remove="loading")
                refresh()
                scroll.scroll_to(percent=1)

        with ui.row().classes("items-center"):
            ui.label(name).classes("font-semibold")
            status = ui.label().classes("text-sm font-medium")
            ui.link(t("Cota e uso da API"), USAGE_URLS[provider], new_tab=True).classes("text-sm")
        unavailable = ui.label(t("Salve a chave deste provedor e ative o acesso online nas configurações.")).classes(
            "text-sm studio-muted"
        )
        with ui.column().classes("w-full gap-2"):
            with ui.row().classes("w-full gap-2"):
                kinds = {"text": t("História ou roteiro"), "image": t("Imagem")}
                if "video" in models:
                    kinds["video"] = t("Vídeo")
                mode = ui.select(kinds, value="text", label=t("Tipo de criação")).classes("w-full sm:flex-1")
                model = ui.select(
                    models["text"],
                    value=models["text"][0],
                    label=t("Modelo"),
                    new_value_mode="add-unique",
                ).classes("w-full sm:flex-1")
                mode.on_value_change(switch_mode)
            with ui.scroll_area().classes("w-full border border-slate-200 rounded-lg bg-white h-80") as scroll:
                results = ui.column().classes("w-full p-3 gap-3")
            prompt = ui.textarea(t("O que você quer criar?")).props("outlined rows=3 maxlength=20000").classes("w-full")
            prompt.on(
                "keydown",
                lambda: generate(),
                js_handler="""event => {
                    if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.altKey
                        || event.metaKey || event.isComposing || event.repeat) return;
                    event.preventDefault();
                    emit();
                }""",
            )
            with ui.row().classes("items-center"):
                voice_mode = ui.switch(t("Modo de voz"), value=False, on_change=toggle_voice)
                ui.button(t("Parar voz"), icon="volume_off", on_click=stop_speaking).props("flat no-caps")
            recorder = Microphone(receive_voice, t, for_chat=True).classes("w-full")
            uploader = ui.upload(
                label=t("Arraste arquivos ou clique para anexar"),
                auto_upload=True,
                multiple=True,
                max_file_size=ATTACHMENT_LIMIT,
                max_total_size=ATTACHMENT_LIMIT,
                max_files=MAX_ATTACHMENTS,
                on_multi_upload=receive_attachments,
                on_rejected=lambda: ui.notify(
                    t("Confira o formato e o limite de 6 anexos, até 12 MB no total."), type="warning"
                ),
            ).classes("w-full")
            pending_files()
            uploader.props(f'accept="{",".join(_attachment_types(provider, current_kind))}"')
            with ui.row():
                submit = ui.button(t("Enviar"), icon="send", on_click=lambda: generate()).props("no-caps")
                revise_button = ui.button(t("Revisar roteiro atual"), on_click=review_current_script).props(
                    "outline no-caps"
                )
                reset_button = ui.button(t("Limpar conversa"), on_click=reset).props("flat no-caps")
        refresh()
        return refresh

    with ui.expansion(t("Consoles de IA"), icon="auto_awesome").classes("w-full") as panel:
        ui.label(t("Os pedidos são enviados ao provedor escolhido e podem consumir créditos da API.")).classes(
            "text-sm studio-muted"
        )
        with ui.tabs().classes("w-full") as tabs:
            gemini_tab = ui.tab("Gemini")
            openai_tab = ui.tab("OpenAI")
        with ui.tab_panels(tabs, value=gemini_tab).classes("w-full"):
            with ui.tab_panel(gemini_tab):
                gemini_refresh = provider_console("gemini", "Gemini")
            with ui.tab_panel(openai_tab):
                openai_refresh = provider_console("openai", "OpenAI")

    def refresh_all():
        gemini_refresh()
        openai_refresh()

    panel.on_value_change(refresh_all)
    return refresh_all
