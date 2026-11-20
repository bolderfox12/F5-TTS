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
    payload = {"text": text, "reference": reference, "language": language, "device": device, "output": str(output)}
    with subprocess.Popen(
        [str(python), "-I", str(Path(__file__).resolve())],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    ) as process:
        request = json.dumps(payload)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                process.kill()
                process.communicate()
                raise SynthesisCancelled
            try:
                _, stderr = process.communicate(request, timeout=0.2 if cancel_event is not None else None)
                break
            except subprocess.TimeoutExpired:
                request = None
        returncode = process.returncode
    if returncode:
        logging.getLogger(__name__).error("Chatterbox exited with status %s\n%s", returncode, stderr)
        raise RuntimeError("O Chatterbox falhou. Confira a instalação, o dispositivo e o log do Studio.")


def main():
    import soundfile as sf
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    request = json.load(sys.stdin)
    device = request["device"]
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    model = ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model="v3")
    model.prepare_conditionals(request["reference"])
    with sf.SoundFile(request["output"], mode="w", samplerate=model.sr, channels=1, subtype="FLOAT") as output:
        # Keep each request short enough for the model's speech token limit.
        for text in wrap(" ".join(request["text"].split()), width=180, break_on_hyphens=False):
            samples = model.generate(text, language_id=request["language"])
            output.write(samples.detach().cpu().reshape(-1).numpy())


if __name__ == "__main__":
    main()
