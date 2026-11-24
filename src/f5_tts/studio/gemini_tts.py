"""Generate editable dubbing clips using the Gemini Interactions API."""

import base64
import binascii
import io
import wave

import httpx

from f5_tts.studio.dubbing_activity import post


MODEL_ID = "gemini-3.8-flash-tts"
VOICES = ("Kore", "Charon", "Puck", "Aoede", "Fenrir", "Leda", "Orus", "Zephyr")
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"


def default_voice(speaker):
    return VOICES[(speaker - 1) % len(VOICES)]


def synthesize(text, voice, style, api_key, destination):
    part = {"type": "text", "text": text}
    if style:
        part["annotations"] = [{"type": "speech_metadata", "style": style}]
    body = {
        "model": MODEL_ID,
        "store": False,
        "input": [{"type": "user_input", "content": [part]}],
        "response_format": {"type": "audio", "mime_type": "audio/wav", "sample_rate": 24000},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }
    try:
        response = post(
            "Gemini TTS",
            "síntese de voz",
            ENDPOINT,
            characters=len(text),
            headers={"x-goog-api-key": api_key},
            json=body,
            timeout=120,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        if status == 429:
            raise ValueError("O Gemini TTS atingiu a cota de solicitações (HTTP 429).") from None
        if status in (500, 502, 503, 504):
            raise ValueError(f"O Gemini TTS está temporariamente indisponível (HTTP {status}).") from None
        raise ValueError(
            f"O Gemini TTS recusou a solicitação (HTTP {status}). Confira a chave e o acesso ao modelo."
        ) from None
    except httpx.TransportError:
        raise ValueError("Não foi possível conectar ao Gemini TTS.") from None

    try:
        audio = next(
            item
            for step in reversed(response.json()["steps"])
            if step["type"] == "model_output"
            for item in reversed(step["content"])
            if item["type"] == "audio"
        )
        data = base64.b64decode(audio["data"], validate=True)
        with wave.open(io.BytesIO(data), "rb") as speech:
            if not speech.getnframes():
                raise ValueError
    except (StopIteration, KeyError, TypeError, ValueError, binascii.Error, EOFError, wave.Error):
        raise ValueError("O Gemini TTS não retornou um áudio WAV válido.") from None
    destination.write_bytes(data)
