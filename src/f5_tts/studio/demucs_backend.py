"""Shared Demucs runner for separating vocals and background music."""

import logging
import os
import shutil
import subprocess
from pathlib import Path


def demucs_command(service):
    configured = os.environ.get("F5_STUDIO_DEMUCS")
    if configured:
        return shutil.which(configured)
    executable = service.home / "demucs" / ".venv" / ("Scripts/demucs.exe" if os.name == "nt" else "bin/demucs")
    return str(executable) if executable.is_file() else shutil.which("demucs")


def _device(command):
    python = Path(command).with_name("python.exe" if os.name == "nt" else "python")
    if not python.is_file():
        return "auto"
    try:
        result = subprocess.run(
            [str(python), "-c", "import torch; print('cuda' if torch.cuda.is_available() else 'cpu')"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "auto"
    device = result.stdout.strip()
    return device if device in ("cpu", "cuda") else "auto"


def separate_track(service, track, directory, *, report=None):
    command = demucs_command(service)
    if not command:
        raise ValueError("Instale o Demucs para separar a voz da música e dos efeitos.")
    settings = service.read()
    weights = service.home / "demucs" / "weights"
    if settings.get("offline") and not (weights / "hub" / "checkpoints" / "955717e8-8726e21a.th").is_file():
        raise ValueError("Baixe os pesos do Demucs antes de separar o fundo no modo offline.")

    device = "cpu" if settings["settings"]["device"] == "cpu" else _device(command)
    args = [command, "--two-stems", "vocals", "-n", "htdemucs", "-o", str(directory)]
    env = {**os.environ, "TORCH_HOME": str(weights), "OMP_NUM_THREADS": "4"}

    def run(selected):
        options = ["-d", selected] if selected != "auto" else []
        subprocess.run([*args, *options, str(track)], check=True, capture_output=True, text=True, timeout=3600, env=env)

    if report:
        messages = {
            "cuda": "Separando voz e fundo com Demucs na GPU…",
            "cpu": "Separando voz e fundo com Demucs na CPU…",
            "auto": "Separando voz e fundo com Demucs…",
        }
        report(0.10, messages[device])
    try:
        try:
            run(device)
        except subprocess.CalledProcessError as error:
            if device == "cpu" or not any(
                word in (error.stderr or "").lower() for word in ("cuda", "cudnn", "out of memory")
            ):
                raise
            if report:
                report(0.10, "CUDA indisponível. Separando voz e fundo na CPU…")
            run("cpu")
        stems = directory / "htdemucs" / track.stem
        vocals, background = stems / "vocals.wav", stems / "no_vocals.wav"
        if not (vocals.is_file() and background.is_file()):
            raise FileNotFoundError("Demucs did not produce both stems")
    except (subprocess.SubprocessError, OSError) as error:
        logging.getLogger(__name__).error("Demucs failed: %s", getattr(error, "stderr", None) or error)
        raise ValueError(
            "O Demucs falhou ao separar voz e fundo. Confira a instalação e os pesos do modelo."
        ) from error
    return vocals, background
