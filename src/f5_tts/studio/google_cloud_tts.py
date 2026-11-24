"""Google Cloud Text-to-Speech synthesis with Application Default Credentials."""

import base64
import io
import json
import re
import tempfile
import wave
from pathlib import Path

import httpx

from f5_tts.studio.dubbing_activity import post


MODEL_ID = "google-cloud-tts"
CLOUD_MODEL = "gemini-2.5-flash-tts"
ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
LOCALES = {
    "en": "en-US",
    "pt": "pt-BR",
    "es": "es-ES",
    "fr": "fr-FR",
    "de": "de-DE",
    "it": "it-IT",
    "ko": "ko-KR",
    "ja": "ja-JP",
    "zh": "cmn-CN",
}
DISABLED_SERVICES = {
    "texttospeech.googleapis.com": "Cloud Text-to-Speech",
    "aiplatform.googleapis.com": "Vertex AI / Agent Platform",
}


def credential_file(service):
    return service.home / "google-cloud-tts-credentials.json"


def _service_account(data):
    from google.auth.exceptions import GoogleAuthError
    from google.oauth2.service_account import Credentials

    try:
        if (
            data["type"] != "service_account"
            or data["token_uri"] != TOKEN_URI
            or not data["client_email"].endswith(".gserviceaccount.com")
            or not re.fullmatch(r"[a-z0-9.:-]+", data["project_id"])
        ):
            raise ValueError
        return Credentials.from_service_account_info(data, scopes=SCOPES), data["project_id"]
    except (AttributeError, KeyError, TypeError, ValueError, GoogleAuthError):
        raise ValueError("Selecione a chave JSON de uma conta de serviço do Google Cloud.") from None


def import_credentials(service, raw):
    try:
        data = json.loads(raw)
    except (UnicodeError, ValueError):
        raise ValueError("Selecione a chave JSON de uma conta de serviço do Google Cloud.") from None
    _service_account(data)
    fields = ("type", "project_id", "private_key_id", "private_key", "client_email", "token_uri")
    service._write_json(credential_file(service), {key: data[key] for key in fields if key in data})
    return True


def credentials(service):
    from google.auth import default
    from google.auth.exceptions import GoogleAuthError

    try:
        data = credential_file(service).read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError):
        raise ValueError("Não foi possível ler a chave salva do Google Cloud.") from None
    else:
        try:
            return _service_account(json.loads(data))
        except ValueError:
            raise ValueError("A chave salva está inválida. Importe novamente em Conectar Google Cloud.") from None
    try:
        return default(scopes=SCOPES)
    except GoogleAuthError:
        raise ValueError("Importe uma chave em Conectar Google Cloud ou configure as credenciais ADC.") from None


def test_connection(service):
    if service.read().get("offline"):
        raise ValueError("Desative o modo offline para testar a conexão com o Google Cloud.")
    with tempfile.TemporaryDirectory(prefix="cloud-tts-test-") as directory:
        synthesize("Olá!", "pt", "Kore", "", credentials(service), Path(directory) / "test.wav")
    return True


def synthesize(text, language, voice, style, auth, destination):
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport.requests import Request

    if len(text.encode("utf-8")) > 4000:
        raise ValueError("O Google Cloud aceita até 4.000 bytes por fala. Divida a fala em trechos menores.")
    account, project = auth
    headers = {}
    try:
        account.before_request(Request(), "POST", ENDPOINT, headers)
    except GoogleAuthError:
        raise ValueError(
            "Não foi possível autenticar no Google Cloud. Confira a chave importada ou as credenciais ADC."
        ) from None
    if project:
        headers.setdefault("x-goog-user-project", project)
    body = {
        "input": {"text": text, **({"prompt": style} if style else {})},
        "voice": {"languageCode": LOCALES[language], "name": voice, "modelName": CLOUD_MODEL},
        "audioConfig": {"audioEncoding": "LINEAR16"},
    }
    try:
        response = post(
            "Google Cloud TTS",
            "síntese de voz",
            ENDPOINT,
            characters=len(text),
            json=body,
            headers=headers,
            timeout=120,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        if status == 429:
            raise ValueError("O Google Cloud Text-to-Speech atingiu a cota do projeto (HTTP 429).") from None
        if status in (401, 403):
            if status == 403:
                try:
                    details = error.response.json()["error"]["details"]
                    disabled = next(
                        (
                            DISABLED_SERVICES[detail["metadata"]["service"]]
                            for detail in details
                            if detail.get("reason") == "SERVICE_DISABLED"
                            and detail.get("metadata", {}).get("service") in DISABLED_SERVICES
                        ),
                        None,
                    )
                except (AttributeError, KeyError, TypeError, ValueError):
                    disabled = None
                if disabled:
                    raise ValueError(
                        f"A API {disabled} está desativada no projeto do Google Cloud. Ative a API e teste novamente."
                    ) from None
            raise ValueError(
                f"O Google Cloud recusou o acesso (HTTP {status}). Confira as credenciais, o faturamento "
                "e as permissões do projeto para Text-to-Speech."
            ) from None
        raise ValueError(f"O Google Cloud Text-to-Speech retornou HTTP {status}.") from None
    except httpx.TransportError:
        raise ValueError("Não foi possível conectar ao Google Cloud Text-to-Speech.") from None

    try:
        data = base64.b64decode(response.json()["audioContent"], validate=True)
        with wave.open(io.BytesIO(data), "rb") as speech:
            if not speech.getnframes():
                raise ValueError
    except (KeyError, TypeError, ValueError, EOFError, wave.Error):
        raise ValueError("O Google Cloud Text-to-Speech não retornou um áudio WAV válido.") from None
    destination.write_bytes(data)
