"""Persistent, single-user workspace. Models are loaded only when requested."""

import base64
import binascii
import gc
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone
from importlib.resources import files
from io import BytesIO
from pathlib import Path, PureWindowsPath
from uuid import uuid4

import numpy as np
import soundfile as sf
import torch

from f5_tts.audio import local_audio_input
from f5_tts.studio import chatterbox_backend as chatterbox
from f5_tts.studio.i18n import INTERFACE_LANGUAGES, translate
from f5_tts.studio.local_tools import LocalTools
from f5_tts.studio.story_tools import StoryTools
from f5_tts.train.guided_training import TrainingProject, import_ready_audio, training_upload_name
from f5_tts.train.studio_dataset import prepare_dataset, vocabulary_digest


class _InferenceProgress:
    def __init__(self, callback):
        self.callback = callback
        self.lock = threading.Lock()
        self.fractions = []

    def update(self, batch_index, step, total_steps, batch_count):
        with self.lock:
            if len(self.fractions) != batch_count:
                self.fractions = [0.0] * batch_count
            self.fractions[batch_index] = min(1.0, step / max(1, total_steps))
            fraction = sum(self.fractions) / batch_count
            completed = sum(value >= 1 for value in self.fractions)
        percent = 8 + 82 * fraction
        self.callback(True, percent, f"Inferindo · {completed}/{batch_count} trechos")


class StudioService(StoryTools, LocalTools):
    def __init__(self, home=None, data_root=None, backend=None, model_factory=None):
        self.home = Path(home or Path.home() / ".f5tts-studio").resolve()
        self.data_root = Path(data_root or files("f5_tts").joinpath("../../data")).resolve()
        self.uploads = self.home / "uploads"
        self.outputs = self.home / "outputs"
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        for folder in (self.uploads, self.outputs, self.data_root):
            folder.mkdir(parents=True, exist_ok=True)
        self._backend = backend
        self._model_factory = model_factory
        self._model = None
        self._model_key = None
        self._operation = threading.Lock()
        self._storage = threading.RLock()
        self._generation_lock = threading.Lock()
        self._generation_status = {"running": False, "percent": 0.0, "message": "Aguardando geração."}
        self._metrics_lock = threading.Lock()
        self._previous_disk_io = None
        self._interface_language = self.read().get("ui_language", "pt-BR")
        if self._interface_language not in INTERFACE_LANGUAGES:
            self._interface_language = "pt-BR"
        self._stop_requested = threading.Event()
        self.render_status = ""
        self._render_cancel = threading.Event()
        self.job = {"running": False, "message": "Nenhum treinamento em execução.", "project": ""}

    def read(self):
        with self._storage:
            path = self.home / "workspace.json"
            return (
                json.loads(path.read_text(encoding="utf-8"))
                if path.exists()
                else {"voices": [], "history": [], "settings": {"device": "auto", "steps": 32, "speed": 1.0}}
            )

    def write(self, data):
        self._write_json(self.home / "workspace.json", data)

    def _write_json(self, destination, data):
        with self._storage:
            temporary = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.home, suffix=".tmp", delete=False
            )
            path = Path(temporary.name)
            try:
                with temporary:
                    json.dump(data, temporary, ensure_ascii=False, indent=2)
                path.replace(destination)
            finally:
                path.unlink(missing_ok=True)

    def interface_language(self):
        return self._interface_language

    def set_interface_language(self, locale):
        if locale not in INTERFACE_LANGUAGES:
            raise ValueError("Escolha um idioma de interface disponível.")
        with self._storage:
            data = self.read()
            data["ui_language"] = locale
            self.write(data)
            self._interface_language = locale

    def translate_ui(self, text, **values):
        return translate(text, self.interface_language(), **values)

    def model_choices(self):
        return {
            **{
                model["id"]: self.translate_ui(model["name"]) if model["id"] == "original" else model["name"]
                for model in self.models()
            },
            chatterbox.MODEL_ID: chatterbox.MODEL_NAME,
        }

    def backend(self):
        if self._backend is None:
            from f5_tts.train import finetune_gradio

            self._backend = finetune_gradio
        return self._backend

    def project(self, name):
        return TrainingProject(self.data_root, name)

    def projects(self):
        return sorted(p.parent.name for p in self.data_root.glob("*/reviewed_examples.json"))

    def upload_path(self, filename):
        suffix = Path(filename).suffix.lower()
        if suffix not in (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"):
            raise ValueError("Escolha um arquivo WAV, MP3, FLAC, OGG, M4A ou AAC.")
        return self.uploads / (uuid4().hex + "-" + training_upload_name(filename))

    def microphone_reference(self, encoded):
        """Convert a short browser recording to a managed, validated PCM WAV."""
        return self._convert_microphone(encoded, self.upload_path("microfone.wav"))

    def transcribe_chat_recording(self, encoded):
        """Transcribe a chat recording without adding it to the workspace."""
        with tempfile.TemporaryDirectory(prefix="f5-studio-chat-") as folder:
            source = self._convert_microphone(encoded, Path(folder) / "recording.wav")
            return self._transcribe_audio(source, "", "openai/whisper-small", save_record=False)

    def _convert_microphone(self, encoded, output):
        if not isinstance(encoded, str) or not encoded or len(encoded) > 800000:
            raise ValueError("Gravação inválida ou muito grande. Grave um trecho de até 30 segundos.")
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Não foi possível ler a gravação do microfone.") from error
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError("Instale o FFmpeg para usar gravações do microfone.")
        try:
            with tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "recording"
                source.write_bytes(audio)
                subprocess.run(
                    [
                        ffmpeg,
                        "-nostdin",
                        "-v",
                        "error",
                        "-y",
                        *local_audio_input(source),
                        "-map",
                        "0:a:0",
                        "-vn",
                        "-t",
                        "30",
                        "-ac",
                        "1",
                        "-ar",
                        "24000",
                        "-c:a",
                        "pcm_s16le",
                        str(output),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
            return self.validate_reference(output, allow_external=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            output.unlink(missing_ok=True)
            raise ValueError("Não foi possível converter a gravação. Grave novamente.") from error
        except Exception:
            output.unlink(missing_ok=True)
            raise

    def managed_upload(self, path):
        if not path:
            raise ValueError("Envie uma gravação pelo aplicativo primeiro.")
        path = Path(path).resolve()
        if not path.is_relative_to(self.uploads) or not path.is_file():
            raise ValueError("Envie uma gravação pelo aplicativo primeiro.")
        return path

    def validate_reference(self, path, allow_external=False):
        if not path:
            raise ValueError("Envie uma gravação pelo aplicativo primeiro.")
        path = Path(path).resolve()
        if not allow_external and not any(path.is_relative_to(root.resolve()) for root in (self.uploads, self.outputs)):
            raise ValueError("Escolha uma gravação enviada ou gerada neste Studio.")
        if not path.is_file():
            raise ValueError("Escolha uma gravação enviada ou gerada neste Studio.")
        try:
            info = sf.info(path)
        except Exception as error:
            raise ValueError("Não foi possível abrir a referência. Converta para WAV, MP3 ou FLAC.") from error
        if not 1 <= info.duration <= 30:
            raise ValueError("Use uma referência de 1 a 30 segundos; prefira até 12 segundos.")
        return str(path)

    def recent_references(self):
        data = self.read()
        saved_paths = {voice["reference"] for voice in data.get("voices", [])}
        candidates = {}
        for entry in data.get("reference_library", []):
            candidates[entry["path"]] = entry.get("transcript", "")
        for entry in data.get("history", []):
            if entry.get("reference") and not candidates.get(entry["reference"]):
                candidates[entry["reference"]] = entry.get("reference_transcript", "")
        for path in self.uploads.glob("*-microfone.wav"):
            candidates.setdefault(str(path), "")

        rows = []
        roots = (self.uploads.resolve(), self.outputs.resolve())
        external = set(data.get("external_assets", []))
        for source, transcript in candidates.items():
            try:
                path = Path(source).resolve()
                if str(path) in saved_paths or not path.is_file():
                    continue
                if str(path) not in external and not any(path.is_relative_to(root) for root in roots):
                    continue
                modified = path.stat().st_mtime
            except OSError:
                continue
            display = path.name
            if len(display) > 33 and display[32] == "-" and all(c in "0123456789abcdef" for c in display[:32]):
                display = display[33:]
            rows.append(
                {
                    "path": str(path),
                    "name": display,
                    "transcript": transcript,
                    "recorded_at": datetime.fromtimestamp(modified).isoformat(timespec="seconds").replace("T", " "),
                    "modified": modified,
                }
            )
        return sorted(rows, key=lambda row: row["modified"], reverse=True)[:50]

    def remember_reference(self, source, transcript=""):
        path = str(self._managed_asset(source))
        with self._storage:
            data = self.read()
            entries = data.setdefault("reference_library", [])
            previous = next((item for item in entries if item["path"] == path), None)
            if previous and not transcript:
                return
            data["reference_library"] = [
                {"path": path, "transcript": transcript},
                *(item for item in entries if item["path"] != path),
            ][:100]
            self.write(data)

    def save_voice(self, name, reference, transcript):
        if not name or not name.strip():
            raise ValueError("Dê um nome à voz.")
        reference = self.validate_reference(reference)
        with self._storage:
            data = self.read()
            voice = {"id": uuid4().hex, "name": name.strip(), "reference": reference, "transcript": transcript or ""}
            data["voices"].append(voice)
            self.write(data)
        return voice

    def save_generated_reference(self, name, audio_path, transcript):
        """Curate one approved generated sample into the reusable voice-reference library."""
        audio_path = Path(audio_path).resolve()
        if not audio_path.is_relative_to(self.outputs.resolve()) or not audio_path.is_file():
            raise ValueError("Escolha um áudio gerado neste Studio.")
        try:
            info = sf.info(audio_path)
        except Exception as error:
            raise ValueError("Não foi possível abrir o áudio gerado.") from error
        if not 1 <= info.duration <= 30:
            raise ValueError("A referência precisa ter de 1 a 30 segundos.")
        if not name or not name.strip() or not transcript or not transcript.strip():
            raise ValueError("Dê um nome à referência e informe exatamente o texto falado.")
        with self._storage:
            data = self.read()
            voice = {
                "id": uuid4().hex,
                "name": name.strip(),
                "reference": str(audio_path),
                "transcript": transcript.strip(),
            }
            data["voices"].append(voice)
            self.write(data)
        return voice

    def settings(self, device, steps, speed, precision=None):
        if device not in ("auto", "cpu", "cuda") or int(steps) not in (16, 32, 48) or not 0.3 <= float(speed) <= 2:
            raise ValueError("Configuração inválida.")
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA não está disponível nesta máquina. Escolha Automático ou CPU.")
        if precision is not None and precision not in ("fp16", "fp32"):
            raise ValueError("Escolha FP16 ou FP32.")
        with self._storage:
            data = self.read()
            settings = data["settings"]
            settings.update(device=device, steps=int(steps), speed=float(speed))
            if precision is not None:
                settings["precision"] = precision
            self.write(data)

    def hardware(self):
        cuda = torch.cuda.is_available()
        return {
            "cuda": cuda,
            "name": torch.cuda.get_device_name(0) if cuda else "CPU",
            "memory": round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1) if cuda else 0,
        }

    def training_hardware(self):
        """Return live GPU capacity and utilization for the training dashboard."""
        if not torch.cuda.is_available():
            return {**self.hardware(), "free": 0, "allocated": 0, "reserved": 0, "utilization": None}
        free, total = torch.cuda.mem_get_info(0)
        result = {
            **self.hardware(),
            "free": round(free / 1024**3, 1),
            "allocated": round(torch.cuda.memory_allocated(0) / 1024**3, 1),
            "reserved": round(torch.cuda.memory_reserved(0) / 1024**3, 1),
            "utilization": None,
        }
        try:
            query = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,temperature.gpu,power.draw",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            values = [value.strip() for value in query.stdout.splitlines()[0].split(",")]
            result.update(utilization=int(values[0]), temperature=int(values[1]), power=round(float(values[2])))
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            pass
        return result

    def set_generation_status(self, running, percent, message):
        with self._generation_lock:
            self._generation_status = {
                "running": bool(running),
                "percent": max(0.0, min(100.0, float(percent))),
                "message": str(message),
            }

    def generation_status(self):
        with self._generation_lock:
            return dict(self._generation_status)

    def training_gpus(self):
        devices = []
        smi_rows = []
        try:
            query = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=uuid,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            smi_rows = [line.strip().split(",") for line in query.stdout.splitlines()]
        except (OSError, subprocess.SubprocessError):
            pass

        count = torch.cuda.device_count() if torch.cuda.is_available() else 0
        by_uuid = {row[0].strip().lower().removeprefix("gpu-"): row[1:] for row in smi_rows if len(row) == 6}
        for index in range(count):
            properties = torch.cuda.get_device_properties(index)
            memory = properties.total_memory
            utilization = temperature = power = used = None
            match = by_uuid.get(str(getattr(properties, "uuid", "")).lower().removeprefix("gpu-"))
            if match:
                try:
                    utilization = float(match[0])
                except ValueError:
                    pass
                try:
                    used = float(match[1]) * 1024**2
                    memory = float(match[2]) * 1024**2
                except ValueError:
                    pass
                try:
                    temperature = float(match[3])
                except ValueError:
                    pass
                try:
                    power = float(match[4])
                except ValueError:
                    pass
            if used is None:
                try:
                    free, memory = torch.cuda.mem_get_info(index)
                    used = memory - free
                except RuntimeError:
                    pass
            devices.append(
                {
                    "index": index,
                    "name": properties.name,
                    "utilization": utilization,
                    "temperature": temperature,
                    "power": power,
                    "used_gib": used / 1024**3 if used is not None else None,
                    "total_gib": memory / 1024**3,
                }
            )
        return devices

    def system_metrics(self, include_training_gpus=False):
        """Capture host and accelerator usage for the Studio dashboards."""
        import psutil

        cpu = psutil.cpu_percent(interval=0.05)
        memory = psutil.virtual_memory()
        disk = shutil.disk_usage(self.data_root if include_training_gpus else self.home)
        counters = psutil.disk_io_counters()
        read_mbps = write_mbps = 0.0
        now = time.monotonic()
        if counters:
            with self._metrics_lock:
                previous = self._previous_disk_io
                if previous:
                    elapsed = max(0.001, now - previous[0])
                    read_mbps = max(0, counters.read_bytes - previous[1]) / elapsed / (1024**2)
                    write_mbps = max(0, counters.write_bytes - previous[2]) / elapsed / (1024**2)
                self._previous_disk_io = (now, counters.read_bytes, counters.write_bytes)
        gpus = self.training_gpus() if include_training_gpus else []
        primary = gpus[0] if gpus else {"name": "CPU"} if include_training_gpus else self.training_hardware()
        return {
            "gpus": gpus,
            "cpu_percent": cpu,
            "ram_used_gb": (memory.total - memory.available) / (1024**3),
            "ram_total_gb": memory.total / (1024**3),
            "ram_percent": memory.percent,
            "disk_used_percent": (disk.used / disk.total * 100) if disk.total else 0,
            "disk_free_gb": disk.free / (1024**3),
            "disk_read_mbps": read_mbps,
            "disk_write_mbps": write_mbps,
            "gpu_name": primary["name"],
            "gpu_percent": primary.get("utilization"),
            "gpu_vram_used_gb": primary.get("used_gib", primary.get("allocated", 0)),
            "gpu_vram_total_gb": primary.get("total_gib", primary.get("memory", 0)),
        }

    def checkpoints(self, project):
        p = self.project(project)
        return [
            path
            for path in self.backend().get_checkpoints_project(p.name, False)[0]
            if not Path(path).name.startswith("pretrained_")
        ]

    def models(self):
        return [
            {
                "id": "original",
                "name": "F5-TTS original · inglês / chinês",
                "checkpoint": "",
                "vocabulary": "",
                "architecture": "F5TTS_v1_Base",
            },
            *self.read().get("models", []),
        ]

    @staticmethod
    def local_model_file(value, suffixes):
        value = str(value or "").strip().strip('"')
        # Accept a Windows path pasted into the WSL-hosted Studio.
        windows = PureWindowsPath(value)
        if windows.drive and len(windows.drive) == 2 and not Path(value).is_absolute():
            value = str(Path("/mnt") / windows.drive[0].lower() / Path(*windows.parts[1:]))
        path = Path(value).expanduser().resolve()
        if not value or not path.is_file() or path.suffix.lower() not in suffixes:
            raise ValueError("Escolha um arquivo existente no formato indicado.")
        return str(path)

    def model_upload_path(self, filename):
        suffix = Path(filename).suffix.lower()
        if suffix not in (".pt", ".pth", ".ckpt", ".safetensors", ".txt"):
            raise ValueError("Escolha um checkpoint .pt/.pth/.ckpt/.safetensors ou um vocabulário .txt.")
        folder = self.home / "models"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / (uuid4().hex + suffix)

    def model_inventory(self):
        """List registered models with their on-disk size and active project references."""
        data = self.read()
        used = set()
        for project in data.get("narrations", []):
            for block in project.get("blocks", []):
                if block.get("model_id"):
                    used.add(block["model_id"])
            for character in project.get("characters", []):
                if character.get("model_id"):
                    used.add(character["model_id"])
        training_files = set()
        for project_file in self.data_root.glob("*/reviewed_examples.json"):
            try:
                options = json.loads(project_file.read_text(encoding="utf-8")).get("training_options", {})
                for field in ("base_checkpoint", "vocabulary"):
                    if options.get(field):
                        training_files.add(Path(options[field]).resolve())
            except (OSError, json.JSONDecodeError):
                continue
        result = []
        registered_paths = set()
        for model in data.get("models", []):
            files = [Path(model[key]).resolve() for key in ("checkpoint", "vocabulary") if model.get(key)]
            registered_paths.update(files)
            sizes = {path: path.stat().st_size for path in files if path.is_file()}
            managed_roots = ((self.home / "models").resolve(), (self.data_root.parent / "ckpts").resolve())
            checkpoint_path = Path(model["checkpoint"]).resolve()
            managed = any(checkpoint_path.is_relative_to(root) for root in managed_roots)
            result.append(
                {
                    **model,
                    "size": sum(sizes.values()),
                    "exists": all(path.is_file() for path in files),
                    "in_use": model["id"] in used or bool(training_files.intersection(files)),
                    "managed": managed,
                    "training_input": bool(training_files.intersection(files)),
                    "project_checkpoint": checkpoint_path.is_relative_to((self.data_root.parent / "ckpts").resolve()),
                }
            )
        for path in sorted((self.home / "models").glob("*")):
            if (
                path.is_file()
                and path.suffix.lower() in (".pt", ".pth", ".ckpt", ".safetensors")
                and path.resolve() not in registered_paths
            ):
                result.append(
                    {
                        "id": "local-file:" + path.name,
                        "name": path.name,
                        "checkpoint": str(path),
                        "vocabulary": "",
                        "architecture": "Checkpoint local não cadastrado",
                        "size": path.stat().st_size,
                        "exists": True,
                        "in_use": path.resolve() in training_files,
                        "training_input": path.resolve() in training_files,
                        "managed": True,
                        "orphan": True,
                    }
                )
        return result

    def delete_model(self, identifier, delete_files=False):
        """Remove a model registration and, when explicitly requested, its managed files."""
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a geração ou o treinamento terminar antes de remover modelos.")
        try:
            with self._storage:
                data = self.read()
                model = next((item for item in data.get("models", []) if item["id"] == identifier), None)
                if model is None and identifier.startswith("local-file:"):
                    path = (self.home / "models" / identifier.removeprefix("local-file:")).resolve()
                    if not path.is_relative_to((self.home / "models").resolve()) or not path.is_file():
                        raise ValueError("O arquivo local não existe ou não pertence ao Studio.")
                    for project_file in self.data_root.glob("*/reviewed_examples.json"):
                        try:
                            options = json.loads(project_file.read_text(encoding="utf-8")).get("training_options", {})
                        except (OSError, json.JSONDecodeError):
                            continue
                        if path in {
                            Path(options[key]).resolve()
                            for key in ("base_checkpoint", "vocabulary")
                            if options.get(key)
                        }:
                            raise ValueError("Este arquivo está selecionado como base de um projeto de treinamento.")
                    freed = path.stat().st_size if delete_files else 0
                    if delete_files:
                        path.unlink()
                    return freed
                if model is None:
                    raise ValueError("Esse modelo não está cadastrado.")
                for project_file in self.data_root.glob("*/reviewed_examples.json"):
                    try:
                        options = json.loads(project_file.read_text(encoding="utf-8")).get("training_options", {})
                    except (OSError, json.JSONDecodeError):
                        continue
                    if any(
                        model.get(key) and Path(model[key]).resolve() == Path(options[field]).resolve()
                        for key, field in (("checkpoint", "base_checkpoint"), ("vocabulary", "vocabulary"))
                        if options.get(field)
                    ):
                        raise ValueError("Este modelo é usado como base em um projeto de treinamento.")
                for project in data.get("narrations", []):
                    blocks = project.get("blocks", [])
                    characters = project.get("characters", [])
                    if any(block.get("model_id") == identifier for block in blocks) or any(
                        character.get("model_id") == identifier for character in characters
                    ):
                        raise ValueError(
                            "Este modelo ainda é usado por uma narração ou história. Troque o modelo nesses projetos antes de apagá-lo."
                        )
                paths = {Path(model[key]).resolve() for key in ("checkpoint", "vocabulary") if model.get(key)}
                managed_roots = ((self.home / "models").resolve(), (self.data_root.parent / "ckpts").resolve())
                deletable = {
                    path
                    for path in paths
                    if path.is_relative_to((self.home / "models").resolve())
                    or (
                        path == Path(model["checkpoint"]).resolve()
                        and any(path.is_relative_to(root) for root in managed_roots)
                    )
                }
                freed = sum(path.stat().st_size for path in deletable if path.is_file()) if delete_files else 0
                if delete_files:
                    if not deletable:
                        raise ValueError(
                            "O checkpoint está fora do armazenamento local controlado pelo Studio; remova o cadastro sem apagar os arquivos externos."
                        )
                    references = [
                        item
                        for item in data.get("models", [])
                        if item["id"] != identifier
                        and deletable.intersection(
                            Path(item[key]).resolve() for key in ("checkpoint", "vocabulary") if item.get(key)
                        )
                    ]
                    if references:
                        raise ValueError(
                            "Outro modelo cadastrado compartilha estes arquivos; remova primeiro o outro cadastro."
                        )
                    for path in deletable:
                        path.unlink(missing_ok=True)
                data["models"] = [item for item in data["models"] if item["id"] != identifier]
                self.write(data)
                if self._model_key and any(
                    str(path) == item for path in paths for item in self._model_key if isinstance(item, str)
                ):
                    self._model, self._model_key = None, None
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                return freed
        finally:
            self._operation.release()

    def register_model(self, name, checkpoint, vocabulary="", architecture="F5TTS_v1_Base", *, quantized=False):
        name = (name or "").strip()
        if not name or len(name) > 100:
            raise ValueError("Dê ao modelo um nome de até 100 caracteres.")
        if architecture not in ("F5TTS_v1_Base", "F5TTS_Base", "F5TTS_Base_bigvgan", "E2TTS_Base"):
            raise ValueError("Escolha a mesma arquitetura usada no treinamento.")
        checkpoint = self.local_model_file(checkpoint, (".pt", ".pth", ".ckpt", ".safetensors"))
        vocabulary = self.local_model_file(vocabulary, (".txt",)) if vocabulary else ""
        if not quantized and Path(checkpoint).suffix.lower() == ".safetensors":
            from f5_tts.model.quantization import is_int8_checkpoint

            quantized = is_int8_checkpoint(checkpoint)
        with self._storage:
            if any(model["name"].casefold() == name.casefold() for model in self.models()):
                raise ValueError("Já existe um modelo com esse nome.")
            data = self.read()
            model = {
                "id": uuid4().hex,
                "name": name,
                "checkpoint": checkpoint,
                "vocabulary": vocabulary,
                "architecture": architecture,
            }
            if quantized:
                model["quantized"] = True
            data.setdefault("models", []).append(model)
            self.write(data)
        return model

    def quantize_model(self, model_id, name, project="", checkpoint=""):
        from f5_tts.model.quantization import export_int8

        name = (name or "").strip()
        if not name or len(name) > 100:
            raise ValueError("Dê ao modelo um nome de até 100 caracteres.")
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução antes de quantizar.")
        try:
            if any(item["name"].casefold() == name.casefold() for item in self.models()):
                raise ValueError("Já existe um modelo com esse nome.")
            if project:
                checkpoint = str(Path(checkpoint).resolve())
                if checkpoint not in {str(Path(path).resolve()) for path in self.checkpoints(project)}:
                    raise ValueError("Selecione um checkpoint deste projeto.")
                architecture = self.training_options(project)["architecture"]
                vocabulary = self.project_vocabulary(project)
            else:
                model = next((item for item in self.models() if item["id"] == model_id), None)
                if model is None:
                    raise ValueError("Escolha um modelo cadastrado.")
                checkpoint, vocabulary, architecture = (model["checkpoint"], model["vocabulary"], model["architecture"])
                if model_id == "original":
                    from f5_tts.checkpoint_io import download_hf_model_file

                    checkpoint = download_hf_model_file("hf://SWivid/F5-TTS/F5TTS_v1_Base/model_1250000.safetensors")

            self._model, self._model_key = None, None
            gc.collect()
            target = self.model_upload_path("modelo.safetensors")
            copied_vocab = target.with_suffix(".txt") if vocabulary else None
            with tempfile.TemporaryDirectory(dir=target.parent) as folder:
                temporary = Path(folder) / target.name
                export_int8(checkpoint, temporary)
                source_size, size = Path(checkpoint).stat().st_size, temporary.stat().st_size
                with self._storage:
                    try:
                        temporary.replace(target)
                        if copied_vocab:
                            shutil.copyfile(vocabulary, copied_vocab)
                        result = self.register_model(
                            name, str(target), str(copied_vocab or ""), architecture, quantized=True
                        )
                    except Exception:
                        target.unlink(missing_ok=True)
                        if copied_vocab:
                            copied_vocab.unlink(missing_ok=True)
                        raise
            return {**result, "source_size": source_size, "size": size}
        finally:
            self._operation.release()

    def generate(
        self,
        text,
        reference=None,
        transcript="",
        voice_id=None,
        project=None,
        checkpoint=None,
        model_id="original",
        output_format="wav",
        output_quality="standard",
        remove_noise=False,
        stereo=False,
        *,
        language=chatterbox.DEFAULT_LANGUAGE,
        cancel_event=None,
        _part_directory=None,
        _part_progress=None,
    ):
        if not text or not text.strip():
            raise ValueError("Escreva o texto que deseja transformar em voz.")
        if output_format not in ("wav", "mp3") or output_quality not in ("high", "standard", "compact"):
            raise ValueError("Escolha um formato e uma qualidade de áudio válidos.")
        if _part_directory is None and not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação atual ou pare o treinamento antes de gerar áudio.")

        def update_progress(running, percent, message):
            if cancel_event is not None and cancel_event.is_set():
                raise chatterbox.SynthesisCancelled
            if _part_progress is not None:
                index, total = _part_progress
                percent = 90 * (index + percent / 100) / total
                message = f"Trecho {index + 1} de {total} · {message}"
            self.set_generation_status(running, percent, message)

        path = None
        try:
            update_progress(True, 1, "Validando texto e referência.")
            data = self.read()
            if voice_id:
                voice = next((v for v in data["voices"] if v["id"] == voice_id), None)
                if not voice:
                    raise ValueError("Selecione uma voz existente.")
                reference, transcript = voice["reference"], transcript or voice["transcript"]
            reference = self.validate_reference(reference or "", allow_external=bool(voice_id))
            using_chatterbox = model_id == chatterbox.MODEL_ID
            vocabulary = ""
            architecture = "F5TTS_v1_Base"
            model_name = "F5-TTS original · inglês / chinês"
            if project:
                if model_id != "original":
                    raise ValueError("Escolha um modelo salvo ou um projeto treinado, sem combinar os dois.")
                if checkpoint not in self.checkpoints(project):
                    raise ValueError("Selecione um checkpoint do projeto.")
                training_options = self.training_options(project)
                vocabulary = self.project_vocabulary(project)
                architecture = training_options["architecture"]
                model_name = project
            elif checkpoint:
                raise ValueError("O checkpoint precisa pertencer a um projeto.")
            elif using_chatterbox:
                if language not in chatterbox.LANGUAGES:
                    raise ValueError("Escolha um idioma compatível com o Chatterbox.")
                python = chatterbox.chatterbox_python(self.home)
                if not python.is_file():
                    raise ValueError("Instale o ambiente do Chatterbox seguindo o guia do Studio.")
                architecture, model_name = chatterbox.MODEL_ID, chatterbox.MODEL_NAME
            else:
                selected = next((model for model in self.models() if model["id"] == model_id), None)
                if selected is None:
                    raise ValueError("Selecione um modelo existente em Meus modelos.")
                if model_id != "original":
                    checkpoint = self.local_model_file(selected["checkpoint"], (".pt", ".pth", ".ckpt", ".safetensors"))
                    vocabulary = (
                        self.local_model_file(selected["vocabulary"], (".txt",)) if selected["vocabulary"] else ""
                    )
                architecture, model_name = selected["architecture"], selected["name"]
            settings = data["settings"]
            version = self.model_version(checkpoint)
            if using_chatterbox:
                if self._model is not None:
                    self._release_speech_model()
            else:
                key = (
                    checkpoint or "",
                    vocabulary,
                    architecture,
                    settings["device"],
                    settings.get("precision"),
                    version,
                    self.model_version(vocabulary),
                )
                if self._model_key != key:
                    update_progress(True, 3, "Carregando modelo e vocoder.")
                    self._release_speech_model()
                    factory = self._model_factory
                    if factory is None:
                        from f5_tts.api import F5TTS

                        factory = F5TTS
                    self._model = factory(
                        model=architecture,
                        ckpt_file=checkpoint or "",
                        vocab_file=vocabulary,
                        device=None if settings["device"] == "auto" else settings["device"],
                        **({"precision": settings["precision"]} if "precision" in settings else {}),
                    )
                    self._model_key = key
            update_progress(True, 7, "Preparando a inferência.")
            identifier = uuid4().hex
            path = (_part_directory or self.outputs) / (identifier + "." + output_format)
            spoken_text = self.pronounce(text.strip(), data.get("pronunciations", {}))
            with tempfile.TemporaryDirectory(prefix="f5-studio-") as temp_dir:
                source = Path(temp_dir) / "generated.wav"
                if using_chatterbox:
                    update_progress(True, 8, "Carregando o Chatterbox e sintetizando o áudio.")
                    chatterbox.synthesize(
                        python, spoken_text, reference, language, settings["device"], source, cancel_event=cancel_event
                    )
                else:
                    progress = _InferenceProgress(update_progress)
                    self._model.infer(
                        ref_file=reference,
                        ref_text=transcript or "",
                        gen_text=spoken_text,
                        nfe_step=settings["steps"],
                        speed=settings["speed"],
                        seed=42,
                        progress=None,
                        progress_callback=progress.update,
                        transcribe_clipped_reference=True,
                        file_wave=str(source),
                    )
                if not source.exists():
                    raise RuntimeError("O modelo não produziu o arquivo de áudio.")
                update_progress(True, 92, "Finalizando e exportando o áudio.")
                self._export_generated_audio(source, path, output_format, output_quality, remove_noise, stereo)
            entry = {
                "id": identifier,
                "text": text.strip(),
                "spoken_text": spoken_text,
                "path": str(path),
                "voice": voice_id,
                "reference": reference,
                "reference_transcript": transcript or "",
                "project": project,
                "checkpoint": checkpoint,
                "model_version": version,
                "model_id": model_id if not project else None,
                "model_name": model_name,
                "architecture": architecture,
                "steps": settings["steps"],
                "speed": settings["speed"],
                "format": output_format,
                "quality": output_quality,
                "noise_reduction": bool(remove_noise),
                "stereo": bool(stereo),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "rating": "",
                "note": "",
            }
            if using_chatterbox:
                entry.update(language=language, steps=None, speed=None)
            update_progress(True, 99, "Finalizando e exportando o áudio.")
            if _part_directory is None:
                self._save_generation(entry)
                self.set_generation_status(False, 100, "Geração concluída.")
            else:
                update_progress(True, 100, "Geração concluída.")
            return entry
        except chatterbox.SynthesisCancelled:
            if path is not None:
                path.unlink(missing_ok=True)
            self.set_generation_status(False, self.generation_status()["percent"], "Síntese cancelada.")
            raise
        except Exception:
            current = self.generation_status()
            self.set_generation_status(False, current["percent"], "A geração não foi concluída.")
            raise
        finally:
            if _part_directory is None:
                self._operation.release()

    def _save_generation(self, entry):
        with self._storage:
            data = self.read()
            data["history"].insert(0, entry)
            self.write(data)

    def generate_reference_turns(
        self,
        parts,
        project,
        checkpoint,
        model_id,
        output_format,
        output_quality,
        noise_reduction,
        stereo,
        *,
        language=chatterbox.DEFAULT_LANGUAGE,
        cancel_event=None,
    ):
        if not 1 <= len(parts) <= 100:
            raise ValueError("Separe o roteiro em 1 a 100 trechos.")
        if output_format not in ("wav", "mp3") or output_quality not in ("high", "standard", "compact"):
            raise ValueError("Escolha um formato e uma qualidade de áudio válidos.")
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação atual ou pare o treinamento antes de gerar áudio.")
        target = self.outputs / f"{uuid4().hex}.{output_format}"
        try:
            self.set_generation_status(True, 1, "Validando os trechos.")
            voices = {voice["id"]: voice for voice in self.read()["voices"]}
            for part in parts:
                voice_id = part.get("voice_id")
                saved = voices.get(voice_id)
                if voice_id and not saved:
                    raise ValueError("Selecione uma voz existente.")
                self.validate_reference(saved["reference"] if saved else part.get("reference"), bool(saved))
            with tempfile.TemporaryDirectory(prefix="f5-studio-turns-") as folder:
                directory = Path(folder)
                joined = directory / "joined.wav"
                with sf.SoundFile(joined, mode="w", samplerate=24000, channels=1, subtype="PCM_16") as audio:
                    last = None
                    spoken = []
                    used_references = {}
                    for index, part in enumerate(parts):
                        if index:
                            audio.write(np.zeros(7200, dtype=np.float32))
                        last = self.generate(
                            part["text"],
                            part.get("reference"),
                            part.get("transcript", ""),
                            part.get("voice_id"),
                            project,
                            checkpoint,
                            model_id,
                            language=language,
                            cancel_event=cancel_event,
                            _part_directory=directory,
                            _part_progress=(index, len(parts)),
                        )
                        self.set_generation_status(
                            True, 90 * (index + 1) / len(parts), f"Trecho {index + 1} de {len(parts)} pronto."
                        )
                        samples, _ = sf.read(last["path"], dtype="float32")
                        audio.write(samples)
                        spoken.append(last["spoken_text"])
                        used_references[last["reference"]] = last["reference_transcript"]
                        Path(last["path"]).unlink()
                if cancel_event is not None and cancel_event.is_set():
                    raise chatterbox.SynthesisCancelled
                self.set_generation_status(True, 92, "Finalizando e exportando o áudio.")
                self._export_generated_audio(joined, target, output_format, output_quality, noise_reduction, stereo)
            self.set_generation_status(True, 98, "Registrando o áudio concluído.")
            entry = {
                **last,
                "id": target.stem,
                "path": str(target),
                "text": "\n\n".join(
                    f"{part['speaker']}: {part['text']}" if part.get("speaker") else part["text"] for part in parts
                ),
                "spoken_text": "\n\n".join(spoken),
                "voice": None,
                "reference": None,
                "reference_transcript": "",
                "turn_count": len(parts),
                "format": output_format,
                "quality": output_quality,
                "noise_reduction": bool(noise_reduction),
                "stereo": bool(stereo),
            }
            for reference, transcript in used_references.items():
                try:
                    self.remember_reference(reference, transcript)
                except ValueError:
                    pass
            if cancel_event is not None and cancel_event.is_set():
                raise chatterbox.SynthesisCancelled
            self.set_generation_status(True, 99, "Registrando o áudio concluído.")
            self._save_generation(entry)
            self.set_generation_status(False, 100, "Geração concluída.")
            return entry
        except chatterbox.SynthesisCancelled:
            target.unlink(missing_ok=True)
            self.set_generation_status(False, self.generation_status()["percent"], "Síntese cancelada.")
            raise
        except Exception:
            target.unlink(missing_ok=True)
            self.set_generation_status(False, self.generation_status()["percent"], "A geração não foi concluída.")
            raise
        finally:
            self._operation.release()

    @staticmethod
    def _export_generated_audio(source, target, output_format, quality, remove_noise, stereo):
        """Apply requested container, encoding quality, and optional output processing."""
        bitrates = {"high": "320k", "standard": "192k", "compact": "128k"}
        subtypes = {"high": "PCM_24", "standard": "PCM_16", "compact": "PCM_16"}
        sample_rates = {"high": 24000, "standard": 24000, "compact": 16000}
        filtered = source
        ffmpeg = shutil.which("ffmpeg")
        if remove_noise:
            if not ffmpeg:
                raise ValueError("Instale o FFmpeg para ativar a redução de ruído.")
            filtered = source.with_name("denoised.wav")
            result = subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-y",
                    "-i",
                    str(source),
                    "-af",
                    "afftdn=nr=10",
                    "-c:a",
                    "pcm_s24le",
                    str(filtered),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise ValueError("O FFmpeg não conseguiu reduzir o ruído deste áudio.")

        if output_format == "mp3":
            if not ffmpeg:
                raise ValueError("Instale o FFmpeg para exportar em MP3.")
            result = subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-y",
                    "-i",
                    str(filtered),
                    "-vn",
                    "-ac",
                    "2" if stereo else "1",
                    "-ar",
                    str(sample_rates[quality]),
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    bitrates[quality],
                    str(target),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise ValueError("Não foi possível exportar o áudio em MP3. Confira o FFmpeg instalado.")
            return

        audio, sample_rate = sf.read(filtered, dtype="float32", always_2d=True)
        if sample_rate != sample_rates[quality]:
            import torchaudio

            samples = torch.from_numpy(audio.T.copy())
            audio = torchaudio.functional.resample(samples, sample_rate, sample_rates[quality]).T.numpy()
        if stereo and audio.shape[1] == 1:
            audio = np.repeat(audio, 2, axis=1)
        sf.write(target, audio, sample_rates[quality], subtype=subtypes[quality])

    def rate(self, identifier, rating, note=""):
        if rating not in ("Ficou bom", "Precisa melhorar"):
            raise ValueError("Escolha uma avaliação.")
        with self._storage:
            data = self.read()
            entry = next((e for e in data["history"] if e["id"] == identifier), None)
            if not entry:
                raise ValueError("Áudio não encontrado no histórico.")
            entry.update(rating=rating, note=note)
            self.write(data)

    def asset_inventory(self):
        """Show local generated audio, saved references and their workspace links."""
        data = self.read()
        uses = {}

        def add_use(path, description):
            if path:
                key = str(Path(path).resolve())
                uses.setdefault(key, []).append(description)

        for voice in data.get("voices", []):
            add_use(voice.get("reference"), "Voz: " + voice.get("name", ""))
        for edit in data.get("audio_edits", []):
            add_use(edit.get("path"), "Recorte de áudio")
        for entry in data.get("history", []):
            add_use(entry.get("path"), "Histórico: " + (entry.get("text", "")[:90]))
        for project in data.get("narrations", []):
            label = project.get("name", "Projeto")
            for block in project.get("blocks", []):
                add_use((block.get("render") or {}).get("path"), "Trecho: " + label)
            mix = project.get("mix") or {}
            add_use(mix.get("path"), "Narração: " + label)
            add_use(mix.get("srt"), "Legendas: " + label)
        for transcript in data.get("transcripts", []):
            for field in ("path", "audio", "txt", "srt"):
                add_use(transcript.get(field), "Transcrição")

        allowed_suffixes = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".srt", ".txt"}
        result = []
        paths = {path.resolve() for root in (self.outputs, self.uploads) for path in root.rglob("*") if path.is_file()}
        paths.update(Path(value).resolve() for value in data.get("external_assets", []) if Path(value).is_file())
        for path in paths:
            if path.suffix.lower() not in allowed_suffixes:
                continue
            roles = uses.get(str(path.resolve()), [])
            if any(role.startswith("Voz:") for role in roles):
                category = "Referência salva"
            elif any(role.startswith("Histórico:") for role in roles):
                category = "Áudio gerado"
            elif any(role.startswith("Recorte de áudio") for role in roles):
                category = "Áudio editado"
            elif path.suffix.lower() in (".srt", ".txt"):
                category = "Legenda ou texto"
            else:
                category = "Upload de áudio"
            stat = path.stat()
            result.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "category": category,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "uses": roles,
                }
            )
        return sorted(result, key=lambda item: item["modified"], reverse=True)

    def audio_waveform(self, source, points=180, analyze=False):
        """Return a compact peak envelope and duration for an audio-library preview."""
        return self._measure_waveform(self._managed_asset(source), points, analyze)

    @staticmethod
    def _measure_waveform(path, points=180, analyze=False):
        info = sf.info(path)
        bucket_size = max(1, (info.frames + points - 1) // points)
        peaks = []
        energy = clipped = samples = 0
        levels = []
        with sf.SoundFile(path) as audio:
            while True:
                block = audio.read(bucket_size, dtype="float32", always_2d=True)
                if not len(block):
                    break
                peak_block = np.abs(block)
                peaks.append(float(peak_block.max()))
                if analyze:
                    block_energy = np.square(block, dtype=np.float64).sum()
                    energy += block_energy
                    clipped += np.count_nonzero(peak_block >= 0.999)
                    samples += block.size
                    levels.append(np.sqrt(block_energy / block.size))
        peak = max(peaks, default=0.0)
        normalized = [round(value / peak, 4) if peak else 0.0 for value in peaks]
        result = {
            "duration": info.duration,
            "sample_rate": info.samplerate,
            "channels": info.channels,
            "peaks": normalized,
        }
        if analyze:
            rms = float(np.sqrt(energy / samples)) if samples else 0.0
            rms_db = 20 * np.log10(max(rms, 1e-6))
            clipping = 100 * clipped / max(samples, 1)
            quiet_threshold = max(0.001, rms * 0.12)
            quiet = sum(level < quiet_threshold for level in levels) / max(len(levels), 1)
            checks = []
            if info.duration < 1:
                checks.append("too_short")
            elif info.duration > 12:
                checks.append("too_long")
            if rms_db < -36:
                checks.append("quiet")
            if clipping >= 0.1:
                checks.append("clipping")
            if quiet > 0.35 and rms_db >= -36:
                checks.append("silence")
            if info.samplerate < 16000:
                checks.append("sample_rate")
            result["quality"] = {"rms_db": float(rms_db), "clipping": clipping, "checks": checks}
        return result

    def _review_keys(self):
        try:
            return json.loads((self.home / "review-credentials.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def save_review_key(self, provider, key):
        from f5_tts.studio.script_review import PROVIDERS

        key = (key or "").strip()
        if not key or provider not in PROVIDERS or not PROVIDERS[provider][0]:
            raise ValueError("Informe a chave da API do provedor escolhido.")
        with self._storage:
            keys = self._review_keys()
            keys[provider] = key
            self._write_json(self.home / "review-credentials.json", keys)

    def review_api_key(self, provider):
        from f5_tts.studio.script_review import PROVIDERS

        variable = PROVIDERS.get(provider, ("",))[0]
        return (self._review_keys().get(provider) or os.environ.get(variable, "").strip()) if variable else ""

    def review_key_active(self, provider):
        return bool(self.review_api_key(provider))

    def suggest_narration(self, text, api_key="", provider="gemini", model="", instructions=""):
        if provider != "ollama" and self.read().get("offline", False):
            raise ValueError("O modo offline está ativo. Use o Ollama local ou desative o modo offline.")
        from f5_tts.studio.script_review import suggest_script

        key = api_key or (self._review_keys().get(provider, "") if provider != "ollama" else "")
        return suggest_script(text, key, provider, model, instructions)

    def analyze_reference_with_gemini(self, source, api_key="", model=""):
        if self.read().get("offline", False):
            raise ValueError("O modo offline está ativo. Desative-o para analisar áudio com Gemini.")
        source = self.validate_reference(str(self._managed_asset(source)), allow_external=True)
        info = sf.info(source)
        if info.frames * info.channels * 2 > 12_000_000:
            raise ValueError("O áudio está grande demais para análise. Selecione um trecho mais curto.")
        audio, rate = sf.read(source, dtype="float32", always_2d=True)
        with BytesIO() as wav:
            sf.write(wav, audio, rate, format="WAV", subtype="PCM_16")
            from f5_tts.studio.script_review import analyze_gemini_reference

            key = api_key or self._review_keys().get("gemini", "")
            return analyze_gemini_reference(wav.getvalue(), key, model, self.interface_language())

    def trim_reference(self, source, start, end):
        if not 1 <= round(float(end) - float(start), 9) <= 30:
            raise ValueError("Use uma referência de 1 a 30 segundos; prefira até 12 segundos.")
        return self.trim_audio(source, start, end)

    def trim_audio(self, source, start_seconds, end_seconds):
        """Save a selected time range as a new WAV in the Studio output library."""
        source_path = self._managed_asset(source)
        info = sf.info(source_path)
        start = float(start_seconds)
        end = float(end_seconds)
        if not 0 <= start < end <= info.duration or end - start < 0.05:
            raise ValueError("Escolha um trecho válido de pelo menos 0,05 segundo.")
        first = max(0, round(start * info.samplerate))
        last = min(info.frames, round(end * info.samplerate))
        if last <= first:
            raise ValueError("O trecho selecionado está vazio.")
        output = self.outputs / f"recorte-{uuid4().hex[:12]}.wav"
        with sf.SoundFile(source_path) as audio:
            audio.seek(first)
            segment = audio.read(last - first, dtype="float32", always_2d=True)
        subtype = info.subtype if info.subtype in sf.available_subtypes("WAV") else "PCM_16"
        sf.write(output, segment, info.samplerate, subtype=subtype)
        result = {"path": str(output), "duration": len(segment) / info.samplerate, "size": output.stat().st_size}
        with self._storage:
            data = self.read()
            data.setdefault("audio_edits", []).insert(
                0,
                {
                    "id": uuid4().hex,
                    **result,
                    "source": str(source_path),
                    "start": start,
                    "end": end,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            self.write(data)
        return result

    def _managed_asset(self, value):
        path = Path(value).expanduser().resolve()
        roots = (self.outputs.resolve(), self.uploads.resolve())
        external_assets = {str(Path(item).resolve()) for item in self.read().get("external_assets", [])}
        if (
            not path.is_file()
            or not any(path.is_relative_to(root) for root in roots)
            and str(path) not in external_assets
        ):
            raise ValueError("Escolha um arquivo do armazenamento local do Studio.")
        return path

    @staticmethod
    def _replace_path(value, old, new):
        if isinstance(value, dict):
            return {key: StudioService._replace_path(item, old, new) for key, item in value.items()}
        if isinstance(value, list):
            return [StudioService._replace_path(item, old, new) for item in value]
        return new if value == old else value

    def organize_asset(self, source, destination, filename="", move=False):
        """Copy or move a managed file to a user-selected folder, updating references on move."""
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a geração ou o treinamento terminar antes de organizar arquivos.")
        try:
            source_path = self._managed_asset(source)
            destination = str(destination or "").strip().strip('"')
            windows = PureWindowsPath(destination)
            if windows.drive and len(windows.drive) == 2 and not Path(destination).is_absolute():
                destination = str(Path("/mnt") / windows.drive[0].lower() / Path(*windows.parts[1:]))
            destination_path = Path(destination).expanduser().resolve()
            if destination_path == source_path or source_path.is_relative_to(destination_path):
                raise ValueError("Escolha uma pasta de destino diferente da pasta atual do arquivo.")
            destination_path.mkdir(parents=True, exist_ok=True)
            desired = (
                Path(filename.replace("\\", "/")).name.strip() if filename and filename.strip() else source_path.name
            )
            if not desired or desired in (".", ".."):
                raise ValueError("Informe um nome de arquivo válido.")
            target = destination_path / desired
            if target.suffix.lower() != source_path.suffix.lower():
                raise ValueError("Mantenha a extensão original do arquivo.")
            stem, suffix, index = target.stem, target.suffix, 1
            while target.exists():
                target = destination_path / f"{stem} ({index}){suffix}"
                index += 1
            shutil.copy2(source_path, target)
            old, new = str(source_path), str(target.resolve())
            with self._storage:
                data = self.read()
                if move:
                    updated = self._replace_path(data, old, new)
                    data = updated
                external_assets = {str(Path(item).resolve()) for item in data.get("external_assets", [])}
                if any(target.resolve().is_relative_to(root.resolve()) for root in (self.outputs, self.uploads)):
                    external_assets.discard(new)
                else:
                    external_assets.add(new)
                if move:
                    external_assets.discard(old)
                data["external_assets"] = sorted(external_assets)
                try:
                    self.write(data)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
            if move:
                source_path.unlink()
            return {"path": str(target), "size": target.stat().st_size, "moved": bool(move)}
        finally:
            self._operation.release()

    def delete_asset(self, source):
        """Delete a local asset and remove or invalidate all workspace entries that point to it."""
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a geração ou o treinamento terminar antes de apagar arquivos.")
        try:
            path = self._managed_asset(source)
            target = str(path)
            size = path.stat().st_size
            with self._storage:
                data = self.read()

                def matches(value):
                    return bool(value) and str(Path(value).resolve()) == target

                removed_voice_ids = {voice["id"] for voice in data.get("voices", []) if matches(voice.get("reference"))}
                for project in data.get("narrations", []):
                    if any(
                        block.get("voice") in removed_voice_ids
                        or block.get("character")
                        in {
                            character["id"]
                            for character in project.get("characters", [])
                            if character.get("voice") in removed_voice_ids
                        }
                        for block in project.get("blocks", [])
                    ):
                        raise ValueError(
                            "Esta referência está em uso por uma narração ou história. Troque a voz nesses projetos antes de apagá-la."
                        )
                data["voices"] = [voice for voice in data.get("voices", []) if voice["id"] not in removed_voice_ids]
                data["history"] = [entry for entry in data.get("history", []) if not matches(entry.get("path"))]
                data["audio_edits"] = [edit for edit in data.get("audio_edits", []) if not matches(edit.get("path"))]
                for project in data.get("narrations", []):
                    for block in project.get("blocks", []):
                        if matches((block.get("render") or {}).get("path")):
                            block["render"] = None
                    mix = project.get("mix") or {}
                    if any(matches(mix.get(field)) for field in ("path", "srt", "txt")):
                        project["mix"] = None
                data["transcripts"] = [
                    record
                    for record in data.get("transcripts", [])
                    if not any(matches(record.get(field)) for field in ("path", "audio", "txt", "srt"))
                ]
                self.write(data)
            external_assets = {str(Path(item).resolve()) for item in data.get("external_assets", [])}
            if target in external_assets:
                data["external_assets"] = sorted(external_assets - {target})
                with self._storage:
                    self.write(data)
            path.unlink()
            return size
        finally:
            self._operation.release()

    def import_audio(self, name, paths, language, consent, transcripts=""):
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução antes de importar.")
        try:
            for path in paths:
                self.managed_upload(path)
            project = self.project(name)
            if transcripts.strip():
                return project.import_transcripts(paths, transcripts, consent)
            return import_ready_audio(project, paths, language, consent, self.backend())
        finally:
            self._operation.release()

    def review(self, name, identifier, text, approved):
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução antes de revisar.")
        try:
            p = self.project(name)
            rows = p.rows()
            if identifier not in {row[0] for row in rows}:
                raise ValueError("Selecione uma gravação na tabela.")
            for row in rows:
                if row[0] == identifier:
                    row[1], row[2] = bool(approved), text
            return p.review(rows)
        finally:
            self._operation.release()

    def prepare(self, name, base_vocabulary="", extend=True):
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução antes de preparar.")
        try:
            project = self.project(name)
            base_vocab = self.training_base_vocabulary(base_vocabulary)
            return prepare_dataset(project, base_vocab, extend, bool(self.checkpoints(name)))
        finally:
            self._operation.release()

    def training_base_vocabulary(self, path):
        if path:
            return self.local_model_file(path, (".txt",))
        return files("f5_tts").joinpath("../../data/Emilia_ZH_EN_pinyin/vocab.txt")

    def project_vocabulary(self, name):
        project = self.project(name)
        options = self.training_options(name)
        if project.read().get("prepared_vocabulary"):
            return str(project.path / "vocab.txt")
        return options["vocabulary"] or str(project.path / "vocab.txt")

    def training_options(self, name):
        project = self.project(name)
        try:
            hardware = self.training_hardware()
        except (RuntimeError, AssertionError):
            hardware = {"memory": 0}
        try:
            supports_bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
        except (RuntimeError, AssertionError):
            supports_bf16 = False
        recommended_batch = max(800, min(6400, int(38400 * max(0, hardware["memory"] - 5) / 75)))
        defaults = {
            "architecture": "F5TTS_v1_Base",
            "learning_rate": 1e-5,
            "batch_size": recommended_batch,
            "max_samples": 8,
            "grad_accumulation": 1,
            "max_grad_norm": 1.0,
            "num_workers": 8,
            "epochs": 20,
            "warmup_updates": 100,
            "save_every": 100,
            "keep_checkpoints": 5,
            "precision": "bf16" if supports_bf16 else "fp16",
            "use_8bit_optimizer": False,
            "activation_checkpointing": False,
            "tf32": False,
            "log_samples": False,
            "seed": 666,
            "validation_percent": 0,
            "multi_gpu": False,
            "ram_cache_gib": 0.0,
            "base_checkpoint": "",
            "vocabulary": "",
        }
        defaults.update(project.read().get("training_options", {}))
        return defaults

    def gpu_training_recommendation(self):
        info = self.training_hardware()
        if not info["cuda"]:
            raise ValueError("Nenhuma GPU NVIDIA/CUDA disponível para recomendar um perfil.")
        # F5 uses frame-budget batching. This conservative estimate follows the
        # upstream trainer's VRAM-based sizing rule, capped for a single 12 GB card.
        batch = max(800, min(6400, int(38400 * max(0, info["memory"] - 5) / 75)))
        return {
            "batch_size": batch,
            "max_samples": 8,
            "grad_accumulation": 1,
            "max_grad_norm": 1.0,
            "num_workers": 8,
            "precision": "bf16" if torch.cuda.is_bf16_supported() else "fp16",
            "use_8bit_optimizer": info["memory"] <= 16,
            "activation_checkpointing": info["memory"] <= 16,
            "tf32": torch.cuda.get_device_capability()[0] >= 8,
            "hardware": info,
        }

    def save_training_options(self, name, options):
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução para salvar as opções.")
        try:
            return self._save_training_options(name, options)
        finally:
            self._operation.release()

    def _save_training_options(self, name, options):
        current = self.training_options(name)
        merged = {**current, **options}
        architectures = ("F5TTS_v1_Base", "F5TTS_Base", "F5TTS_Base_bigvgan", "E2TTS_Base")
        if merged["architecture"] not in architectures:
            raise ValueError("Escolha uma arquitetura F5-TTS válida.")
        if not 1e-7 <= float(merged["learning_rate"]) <= 1e-3:
            raise ValueError("A taxa de aprendizado deve ficar entre 0,0000001 e 0,001.")
        if not 400 <= int(merged["batch_size"]) <= 50000:
            raise ValueError("O orçamento de batch em frames deve ficar entre 400 e 50.000.")
        if not 1 <= int(merged["max_samples"]) <= 256 or not 1 <= int(merged["grad_accumulation"]) <= 64:
            raise ValueError("Verifique o limite de amostras e a acumulação de gradiente.")
        if not 0.1 <= float(merged["max_grad_norm"]) <= 10 or not 0 <= int(merged["num_workers"]) <= 32:
            raise ValueError("Verifique o limite de gradiente e o número de workers.")
        if not 1 <= int(merged["epochs"]) <= 10000 or not 0 <= int(merged["warmup_updates"]) <= 100000:
            raise ValueError("Verifique épocas e warmup.")
        if not 10 <= int(merged["save_every"]) <= 100000 or not 1 <= int(merged["keep_checkpoints"]) <= 1000:
            raise ValueError("Verifique a frequência e o limite de checkpoints.")
        if merged["precision"] not in ("fp16", "bf16", "none"):
            raise ValueError("Escolha fp16, bf16 ou sem precisão mista.")
        if merged["precision"] == "bf16" and torch.cuda.is_available() and not torch.cuda.is_bf16_supported():
            raise ValueError("Esta GPU não oferece suporte a BF16. Escolha FP16.")
        if merged["activation_checkpointing"] and merged["architecture"] == "E2TTS_Base":
            raise ValueError("O checkpoint de ativações está disponível nas arquiteturas F5-TTS.")
        ram_cache = float(merged["ram_cache_gib"])
        if not 0 <= ram_cache <= 1024:
            raise ValueError("O limite de cache deve ficar entre 0 e 1024 GiB.")
        merged["ram_cache_gib"] = ram_cache
        if not 0 <= int(merged["validation_percent"]) <= 30:
            raise ValueError("Reserve de 0 a 30% dos exemplos para validação.")
        if not 0 <= int(merged["seed"]) <= 2**32 - 1:
            raise ValueError("Use uma semente entre 0 e 4294967295.")
        merged["learning_rate"] = float(merged["learning_rate"])
        merged["max_grad_norm"] = float(merged["max_grad_norm"])
        for field in (
            "batch_size",
            "max_samples",
            "grad_accumulation",
            "epochs",
            "warmup_updates",
            "save_every",
            "keep_checkpoints",
            "num_workers",
            "seed",
            "validation_percent",
        ):
            merged[field] = int(merged[field])
        if merged["base_checkpoint"]:
            from f5_tts.model.quantization import is_int8_checkpoint

            merged["base_checkpoint"] = self.local_model_file(
                merged["base_checkpoint"], (".pt", ".pth", ".ckpt", ".safetensors")
            )
            if is_int8_checkpoint(merged["base_checkpoint"]):
                raise ValueError("Use um checkpoint de treino em precisão normal como modelo inicial.")
        if merged["vocabulary"]:
            merged["vocabulary"] = self.local_model_file(merged["vocabulary"], (".txt",))
        project = self.project(name)
        data = project.read()
        origin = data.get("training_origin", {})
        if any(merged[field] != value for field, value in origin.items()):
            raise ValueError("A arquitetura e o checkpoint inicial pertencem ao histórico deste projeto.")
        data["training_options"] = merged
        project.write(data)
        return merged

    def register_project_checkpoint(self, name, project_name, checkpoint, architecture):
        project = self.project(project_name)
        allowed = {str(Path(path).resolve()) for path in self.checkpoints(project.name)}
        checkpoint = str(Path(checkpoint).resolve())
        if checkpoint not in allowed or not Path(checkpoint).is_file():
            raise ValueError("Atualize e escolha um checkpoint existente neste projeto.")
        options = self.training_options(project.name)
        if architecture != options["architecture"]:
            raise ValueError("Use a arquitetura que foi selecionada para este projeto de treinamento.")
        vocabulary = self.project_vocabulary(project.name)
        return self.register_model(name, checkpoint, vocabulary if Path(vocabulary).exists() else "", architecture)

    def start_training(self, name, options=None):
        if not self._operation.acquire(blocking=False):
            raise ValueError("Já existe uma operação em execução.")
        try:
            return self._start_training(name, options)
        except BaseException:
            self._operation.release()
            raise

    def _start_training(self, name, options):
        p = self.project(name)
        data = p.read()
        if data.get("prepared") != p.fingerprint() or not (p.path / "raw.arrow").exists():
            raise ValueError("Revise e prepare os exemplos antes de treinar.")
        if not torch.cuda.is_available():
            raise ValueError("O treinamento guiado requer GPU NVIDIA/CUDA. A preparação funciona em CPU.")
        if options is None:
            options = self.training_options(name)
        else:
            options = self._save_training_options(name, {"epochs": options} if isinstance(options, int) else options)
        data = p.read()
        origin = {"architecture": options["architecture"], "base_checkpoint": options["base_checkpoint"]}
        gpu_count = torch.cuda.device_count() if options["multi_gpu"] else 1
        if options["multi_gpu"] and gpu_count < 2:
            raise ValueError("O treino com múltiplas GPUs requer pelo menos duas GPUs CUDA visíveis.")
        if (
            options["multi_gpu"]
            and options["precision"] == "bf16"
            and any(torch.cuda.get_device_capability(index)[0] < 8 for index in range(gpu_count))
        ):
            raise ValueError("Uma das GPUs selecionadas não tem BF16 nativo. Escolha FP16 ou precisão total.")
        if (options["multi_gpu"] or options["ram_cache_gib"]) and not data.get("prepared_vocabulary"):
            raise ValueError("Prepare novamente o conjunto para usar múltiplas GPUs ou cache em RAM.")
        studio_args = None
        if data.get("prepared_vocabulary"):
            vocabulary = p.path / "vocab.txt"
            if data["prepared_vocabulary"] != vocabulary_digest(vocabulary) or data[
                "prepared_base_vocabulary"
            ] != vocabulary_digest(self.training_base_vocabulary(options["vocabulary"])):
                raise ValueError("Prepare novamente com o vocabulário base selecionado.")
            durations = json.loads((p.path / "duration.json").read_text(encoding="utf-8"))["duration"]
            if options["validation_percent"] and len(durations) < 5:
                raise ValueError("A validação requer pelo menos cinco exemplos aprovados.")
            min_frames = int(max(durations) * 24000 / 256) + 1
            if options["batch_size"] < min_frames:
                raise ValueError(f"Use pelo menos {min_frames} frames por lote para incluir o trecho mais longo.")
            signature = {
                "dataset": data["prepared"],
                "vocabulary": data["prepared_vocabulary"],
                **{
                    field: options[field]
                    for field in (
                        "seed",
                        "validation_percent",
                        "batch_size",
                        "max_samples",
                        "grad_accumulation",
                        "use_8bit_optimizer",
                    )
                },
            }
            has_resume = any(Path(path).name.startswith("model_") for path in self.checkpoints(p.name))
            signature["gpu_count"] = gpu_count
            previous = {"gpu_count": 1, **data.get("training_signature", {})}
            if has_resume and (previous["gpu_count"] != gpu_count or ("dataset" in previous and previous != signature)):
                raise ValueError(
                    "Para retomar, use os mesmos dados, quantidade de GPUs e parâmetros de lote, semente, validação e otimizador."
                )
            data["training_signature"] = signature
        if (p.path / "vocab.txt").is_file():
            studio_args = {
                "dataset_path": str(p.path),
                "base_vocabulary_rows": data.get("base_vocabulary_rows"),
                "activation_checkpointing": options["activation_checkpointing"],
                "tf32": options["tf32"],
                "log_samples": options["log_samples"],
                "seed": options["seed"],
                "validation_percent": options["validation_percent"],
                "gpu_count": gpu_count,
                "ram_cache_gib": options["ram_cache_gib"],
            }
        if not data.get("training_origin"):
            data["training_origin"] = origin
        p.write(data)
        self._stop_requested.clear()
        self.job = {
            "running": True,
            "message": "Iniciando treinamento…",
            "project": p.name,
            "log": deque(maxlen=120),
            "progress": None,
        }

        def worker():
            try:
                backend = self.backend()
                if self._stop_requested.is_set():
                    self.job["message"] = "Treinamento cancelado antes de iniciar."
                    return
                if backend.training_process is not None:
                    raise ValueError("Já existe um treinamento no aplicativo avançado.")
                self._model, self._model_key = None, None
                gc.collect()
                torch.cuda.empty_cache()
                for message, _start, _stop in backend.start_training(
                    p.name,
                    options["architecture"],
                    options["learning_rate"],
                    options["batch_size"],
                    "frame",
                    options["max_samples"],
                    options["grad_accumulation"],
                    options["max_grad_norm"],
                    options["epochs"],
                    options["warmup_updates"],
                    options["save_every"],
                    options["keep_checkpoints"],
                    options["save_every"],
                    True,
                    options["base_checkpoint"],
                    "pinyin",
                    self.project_vocabulary(p.name) if studio_args else options["vocabulary"],
                    options["precision"],
                    bool(studio_args),
                    "none",
                    options["use_8bit_optimizer"],
                    options["num_workers"],
                    *([studio_args] if studio_args else []),
                ):
                    line = str(message).strip()
                    progress = line.split()
                    if (
                        len(progress) == 5
                        and progress[0] == "F5_STUDIO_PROGRESS"
                        and all(value.isdecimal() for value in progress[1:])
                    ):
                        self.job["progress"] = tuple(map(int, progress[1:]))
                    else:
                        self.job["message"] = str(message)
                        self.job["log"].append(line)
                    if self._stop_requested.is_set():
                        if backend.training_process is None:
                            self.job["message"] = "Treinamento cancelado antes de iniciar."
                            break
                        if not getattr(backend, "stop_signal", False):
                            backend.stop_training()
                if self._stop_requested.is_set():
                    self.job["message"] = "Treinamento interrompido."
            except Exception as error:
                self.job["message"] = "Falha: " + str(error)
            finally:
                self.job["running"] = False
                self._operation.release()

        threading.Thread(target=worker, daemon=True).start()
        return {**self.job, "log": list(self.job["log"])}

    def stop_training(self):
        if not self.job["running"]:
            return "Não há treinamento em execução."
        self._stop_requested.set()
        if self._backend is None or self._backend.training_process is None:
            return "Cancelamento solicitado. Aguarde a preparação terminar."
        return self._backend.stop_training()[0]
