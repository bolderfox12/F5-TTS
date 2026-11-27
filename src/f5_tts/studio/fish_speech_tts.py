"""Client for a separately installed local Fish Speech S2-Pro API server."""

import io
import os
import wave
from pathlib import Path

import httpx
import msgpack

MODEL_ID = "fish-speech-s2-pro"
MODEL_NAME = "Fish Speech S2-Pro · voz expressiva local"
DEFAULT_URL = "http://127.0.0.1:8080/v1/tts"


def synthesize(text, reference, transcript, output, *, cancel_event=None):
    from f5_tts.studio.chatterbox_backend import SynthesisCancelled

    if cancel_event is not None and cancel_event.is_set():
        raise SynthesisCancelled
    transcript = (transcript or "").strip()
    if not transcript:
        raise ValueError(
            "O Fish Speech precisa da transcrição da referência de voz. "
            "Preencha o texto da referência e tente novamente."
        )
    url = os.environ.get("F5_FISH_SPEECH_URL", DEFAULT_URL).strip().rstrip("/")
    if not url:
        url = DEFAULT_URL
    headers = {"content-type": "application/msgpack"}
    api_key = os.environ.get("F5_FISH_SPEECH_API_KEY", "").strip()
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    payload = {
        "text": text.strip(),
        "references": [{"audio": Path(reference).read_bytes(), "text": transcript}],
        "format": "wav",
    }
    try:
        with httpx.Client(trust_env=False, timeout=httpx.Timeout(600, connect=8)) as client:
            response = client.post(
                url,
                params={"format": "msgpack"},
                content=msgpack.packb(payload, use_bin_type=True),
                headers=headers,
            )
            response.raise_for_status()
    except httpx.ConnectError as error:
        raise ValueError(
            "Não foi possível conectar ao Fish Speech. Inicie o servidor S2-Pro em http://127.0.0.1:8080 "
            "ou configure F5_FISH_SPEECH_URL."
        ) from error
    except httpx.HTTPStatusError as error:
        detail = error.response.text[:300]
        raise ValueError(f"Fish Speech retornou HTTP {error.response.status_code}: {detail}") from error
    except httpx.TimeoutException as error:
        raise ValueError("A geração do Fish Speech excedeu o tempo limite de 10 minutos.") from error
    if cancel_event is not None and cancel_event.is_set():
        raise SynthesisCancelled
    audio = response.content
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if wav.getnframes() == 0:
                raise ValueError("O Fish Speech gerou um áudio vazio.")
    except (wave.Error, EOFError) as error:
        raise ValueError("O Fish Speech não retornou um arquivo WAV válido.") from error
    with open(output, "wb") as destination:
        destination.write(audio)
