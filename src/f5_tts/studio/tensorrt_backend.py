"""Optional client for the project's TensorRT-LLM/Triton F5-TTS runtime."""

from urllib.parse import quote

import httpx
import numpy as np
import soundfile as sf


SAMPLE_RATE = 24000


def _base_url(url):
    url = (url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("Informe o endereço HTTP do servidor Triton (por exemplo, http://localhost:8000).")
    return url


def check_connection(url, model_name="f5_tts", timeout=5):
    """Raise a readable error unless Triton and the configured model are ready."""
    base = _base_url(url)
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(f"{base}/v2/health/ready")
            response.raise_for_status()
            response = client.get(f"{base}/v2/models/{quote(model_name, safe='')}/ready")
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise RuntimeError(f"Não foi possível conectar ao Triton ou o modelo {model_name!r} não está pronto: {error}") from error
    return True


def synthesize(url, reference_path, reference_text, target_text, destination, model_name="f5_tts", timeout=300):
    """Generate one WAV from a Triton TensorRT-LLM model using its v2 HTTP API."""
    if not reference_text or not reference_text.strip():
        raise ValueError("O TensorRT-LLM precisa da transcrição do áudio de referência. Informe o texto falado na referência.")
    if not target_text or not target_text.strip():
        raise ValueError("O texto para sintetizar está vazio.")
    audio, sample_rate = sf.read(reference_path, dtype="float32", always_2d=True)
    waveform = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        target_length = round(len(waveform) * SAMPLE_RATE / sample_rate)
        waveform = np.interp(
            np.linspace(0, max(0, len(waveform) - 1), target_length),
            np.arange(len(waveform)),
            waveform,
        ).astype(np.float32, copy=False)
    if not waveform.size or not np.isfinite(waveform).all():
        raise ValueError("O áudio de referência está vazio ou inválido.")
    data = {
        "inputs": [
            {"name": "reference_wav", "shape": [1, len(waveform)], "datatype": "FP32", "data": [waveform.tolist()]},
            {"name": "reference_wav_len", "shape": [1, 1], "datatype": "INT32", "data": [[len(waveform)]]},
            {"name": "reference_text", "shape": [1, 1], "datatype": "BYTES", "data": [reference_text.strip()]},
            {"name": "target_text", "shape": [1, 1], "datatype": "BYTES", "data": [target_text.strip()]},
        ]
    }
    base = _base_url(url)
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(f"{base}/v2/models/{quote(model_name, safe='')}/infer", json=data)
            response.raise_for_status()
            result = response.json()
    except httpx.HTTPStatusError as error:
        detail = error.response.text[:500]
        raise RuntimeError(f"Triton recusou a síntese ({error.response.status_code}): {detail}") from error
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError(f"Falha na comunicação com o TensorRT-LLM: {error}") from error
    try:
        output = next(item for item in result["outputs"] if item.get("name") in ("waveform", "output"))
        generated = np.asarray(output["data"], dtype=np.float32).reshape(-1)
    except (KeyError, StopIteration, TypeError, ValueError) as error:
        raise RuntimeError("O Triton retornou uma resposta sem waveform válida.") from error
    if not generated.size or not np.isfinite(generated).all():
        raise RuntimeError("O TensorRT-LLM retornou áudio vazio ou inválido.")
    sf.write(destination, generated, SAMPLE_RATE, subtype="PCM_16")
    return len(generated) / SAMPLE_RATE
