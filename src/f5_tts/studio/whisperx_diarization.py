"""Optional WhisperX runtime isolated from the Studio's torch environment."""

import json
import hashlib
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path


def runtime_dir(home):
    return Path(home) / "whisperx-runtime"


def python_path(home):
    root = runtime_dir(home)
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed(home):
    return python_path(home).is_file() and (runtime_dir(home) / ".ready").is_file()


def _run_install(command, cancel_event=None):
    from f5_tts.studio.chatterbox_backend import SynthesisCancelled

    try:
        if cancel_event is None:
            subprocess.run(command, check=True, capture_output=True, text=True)
        else:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            while True:
                if cancel_event.is_set():
                    process.kill()
                    process.communicate()
                    raise SynthesisCancelled
                try:
                    stdout, stderr = process.communicate(timeout=0.25)
                    if process.returncode:
                        raise subprocess.CalledProcessError(process.returncode, command, stdout, stderr)
                    break
                except subprocess.TimeoutExpired:
                    continue
    except SynthesisCancelled:
        raise
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip().splitlines()
        message = detail[-1] if detail else "erro sem detalhes"
        raise ValueError(f"Não foi possível instalar WhisperX: {message[:350]}") from error


def _checkpoint_path(home, audio, language, model_id, speaker_count, diarize_only):
    audio_digest = hashlib.sha256()
    with Path(audio).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            audio_digest.update(block)
    identity = json.dumps(
        [audio_digest.hexdigest(), language, model_id, bool(diarize_only)], separators=(",", ":")
    )
    key = hashlib.sha256(identity.encode()).hexdigest()
    cache_dir = Path(home) / "outputs" / "whisperx-checkpoints"
    return cache_dir, cache_dir / f"{key}.json", key


def clear_checkpoint(home, audio, language, model_id, speaker_count=None, *, diarize_only=False):
    try:
        _, checkpoint, _ = _checkpoint_path(home, audio, language, model_id, speaker_count, diarize_only)
        checkpoint.unlink(missing_ok=True)
    except OSError:
        pass


def install(home, report=None, cancel_event=None):
    uv = shutil.which("uv")
    if not uv:
        raise ValueError("Instale o uv para preparar o ambiente isolado do WhisperX.")
    root = runtime_dir(home)
    root.mkdir(parents=True, exist_ok=True)
    python = python_path(home)
    if not python.exists():
        if report:
            report("Preparando Python 3.11 isolado para WhisperX…")
        _run_install([uv, "venv", "--python", "3.11", "--managed-python", str(root / ".venv")], cancel_event)
    if report:
        report("Instalando WhisperX estável e diarização pyannote…")
    _run_install(
        [uv, "pip", "install", "--python", str(python), "whisperx==3.8.6"],
        cancel_event,
    )
    (root / ".ready").touch()
    return python


def analyze(home, audio, language, model_id, speaker_count=None, token="", report=None, *, diarize_only=False, cancel_event=None):
    from f5_tts.studio.chatterbox_backend import SynthesisCancelled

    python = python_path(home)
    if not python.is_file():
        raise ValueError("WhisperX não está instalado. Use o botão Instalar WhisperX nesta tela.")
    cache_dir, checkpoint, cache_key = _checkpoint_path(
        home, audio, language, model_id, speaker_count, diarize_only
    )
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        if checkpoint.exists():
            checkpoint.touch()
        previous = sorted(cache_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for obsolete in previous[20:]:
            try:
                obsolete.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        checkpoint = None
        cache_key = None
    request = {
        "audio": str(Path(audio).resolve()),
        "language": language,
        "model": "large-v3-turbo" if "turbo" in model_id else "small",
        "speaker_count": speaker_count,
        "diarize_only": diarize_only,
        "cache_key": cache_key,
        "checkpoint": str(checkpoint),
    }
    env = os.environ.copy()
    if token:
        env["HF_TOKEN"] = token.strip()
    process = subprocess.Popen(
        [str(python), str(Path(__file__).with_name("whisperx_worker.py"))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    events = queue.Queue()

    def read_output():
        for line in process.stdout:
            events.put(line)
        events.put(None)

    threading.Thread(target=read_output, daemon=True).start()
    process.stdin.write(json.dumps(request))
    process.stdin.close()
    result = None
    tail = []
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                process.kill()
                process.wait()
                raise SynthesisCancelled
            try:
                line = events.get(timeout=0.2 if cancel_event is not None else 7200)
            except queue.Empty:
                if cancel_event is not None:
                    continue
                raise
            if line is None:
                break
            tail.append(line.strip())
            del tail[:-20]
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "progress" in item and report:
                report(item["progress"], item.get("message", "WhisperX"))
            if "segments" in item:
                result = item
            if "error" in item:
                raise ValueError(item["error"])
        return_code = process.wait(timeout=15)
    except queue.Empty as error:
        process.kill()
        process.wait()
        raise ValueError("A análise WhisperX excedeu o limite de duas horas.") from error
    if return_code or result is None:
        message = tail[-1] if tail else "Falha no processo WhisperX."
        if "gated" in message.lower() or "401" in message or "403" in message:
            message = "O pyannote exige token Hugging Face e aceite dos termos dos modelos speaker-diarization-community-1 e segmentation-3.0."
        raise ValueError(message[:600])
    return result
