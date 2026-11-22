"""Run Chatterbox with the Python interpreter from its own virtual environment."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from textwrap import wrap

MODEL_ID = "chatterbox-multilingual-v3"
MODEL_NAME = "Chatterbox Multilingual V3"
DEFAULT_LANGUAGE = "pt"
LANGUAGES = {
    "pt": "Português",
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "ar": "العربية",
    "da": "Dansk",
    "el": "Ελληνικά",
    "fi": "Suomi",
    "he": "עברית",
    "hi": "हिन्दी",
    "ja": "日本語",
    "ko": "한국어",
    "ms": "Bahasa Melayu",
    "nl": "Nederlands",
    "no": "Norsk",
    "pl": "Polski",
    "ru": "Русский",
    "sv": "Svenska",
    "sw": "Kiswahili",
    "tr": "Türkçe",
    "zh": "中文",
}


class SynthesisCancelled(Exception):
    """The caller cancelled a synthesis request."""


def chatterbox_python(workspace):
    environment = workspace / "chatterbox" / ".venv"
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def synthesize(python, text, reference, language, device, output, *, cancel_event=None):
    synthesize_cast(python, [(reference, [(text, output)])], language, device, cancel_event=cancel_event)


def synthesize_cast(python, cast, language, device, *, offline=False, cancel_event=None, on_progress=None):
    payload = {
        "cast": [
            {"reference": str(reference), "segments": [(text, str(path)) for text, path in segments]}
            for reference, segments in cast
        ],
        "language": language,
        "device": device,
    }
    paths = [Path(path) for actor in payload["cast"] for _, path in actor["segments"]]

    def report():
        on_progress(sum(path.is_file() for path in paths))

    _run(python, payload, offline=offline, cancel_event=cancel_event, on_poll=report if on_progress else None)


def identify_voices(python, samples, output, *, offline=False):
    _run(python, {"samples": [str(path) for path in samples], "output": str(output)}, offline=offline)
    return json.loads(output.read_text(encoding="utf-8"))


def _run(python, payload, *, offline=False, cancel_event=None, on_poll=None):
    with subprocess.Popen(
        [str(python), "-I", str(Path(__file__).resolve())],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "HF_HUB_OFFLINE": "1"} if offline else None,
    ) as process:
        request = json.dumps(payload)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                process.kill()
                process.communicate()
                raise SynthesisCancelled
            try:
                _, stderr = process.communicate(request, timeout=0.3 if cancel_event is not None or on_poll else None)
                break
            except subprocess.TimeoutExpired:
                request = None
                if on_poll:
                    on_poll()
        returncode = process.returncode
    if returncode:
        logging.getLogger(__name__).error("Chatterbox exited with status %s\n%s", returncode, stderr)
        raise RuntimeError("O Chatterbox falhou. Confira a instalação, o dispositivo e o log do Studio.")
    if on_poll:
        on_poll()


def _speaker_embeddings(request):
    import soundfile as sf
    import torch
    from chatterbox.models.voice_encoder import VoiceEncoder
    from chatterbox.mtl_tts import REPO_ID
    from huggingface_hub import hf_hub_download

    weights = hf_hub_download(REPO_ID, "ve.pt")
    encoder = VoiceEncoder()
    encoder.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
    encoder.eval()
    embeddings = []
    for path in request["samples"]:
        samples, rate = sf.read(path, dtype="float32")
        embeddings.append(encoder.embeds_from_wavs([samples], sample_rate=rate).mean(axis=0).tolist())
    Path(request["output"]).write_text(json.dumps(embeddings), encoding="utf-8")


def main():
    request = json.load(sys.stdin)
    if "samples" in request:
        _speaker_embeddings(request)
        return

    import soundfile as sf
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    device = request["device"]
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    model = ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model="v3")
    for actor in request["cast"]:
        model.prepare_conditionals(actor["reference"])
        for text, path in actor["segments"]:
            target = Path(path)
            draft = target.with_name(target.name + ".part")
            with sf.SoundFile(
                draft, mode="w", format="WAV", samplerate=model.sr, channels=1, subtype="FLOAT"
            ) as output:
                # Keep each request short enough for the model's speech token limit.
                for part in wrap(" ".join(text.split()), width=180, break_on_hyphens=False):
                    samples = model.generate(part, language_id=request["language"])
                    output.write(samples.detach().cpu().reshape(-1).numpy())
            draft.replace(target)


if __name__ == "__main__":
    main()
