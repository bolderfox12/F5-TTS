"""Persistent, single-user workspace. Models are loaded only when requested."""

import base64
import binascii
import gc
import json
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path, PureWindowsPath
from uuid import uuid4

import soundfile as sf
import torch

from f5_tts.studio.i18n import INTERFACE_LANGUAGES, translate
from f5_tts.studio.local_tools import LocalTools
from f5_tts.studio.story_tools import StoryTools
from f5_tts.train.guided_training import TrainingProject, import_ready_audio


class StudioService(StoryTools, LocalTools):
    def __init__(self, home=None, data_root=None, backend=None, model_factory=None):
        self.home = Path(home or Path.home() / ".f5tts-studio").resolve()
        self.data_root = Path(data_root or files("f5_tts").joinpath("../../data")).resolve()
        self.uploads = self.home / "uploads"
        self.outputs = self.home / "outputs"
        for folder in (self.uploads, self.outputs, self.data_root):
            folder.mkdir(parents=True, exist_ok=True)
        self._backend = backend
        self._model_factory = model_factory
        self._model = None
        self._model_key = None
        self._operation = threading.Lock()
        self._storage = threading.RLock()
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
        with self._storage:
            path = self.home / (uuid4().hex + ".tmp")
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            path.replace(self.home / "workspace.json")

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
            model["id"]: self.translate_ui(model["name"]) if model["id"] == "original" else model["name"]
            for model in self.models()
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
        stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in Path(filename).stem)[:60]
        return self.uploads / (uuid4().hex + "-" + stem + suffix)

    def microphone_reference(self, encoded):
        """Convert a short browser recording to a managed, validated PCM WAV."""
        if not isinstance(encoded, str) or not encoded or len(encoded) > 800000:
            raise ValueError("Gravação inválida ou muito grande. Grave um trecho de até 30 segundos.")
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Não foi possível ler a gravação do microfone.") from error
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError("Instale o FFmpeg para usar gravações do microfone.")
        output = self.upload_path("microfone.wav")
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
                        "-i",
                        str(source),
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
            return self.validate_reference(output)
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

    def settings(self, device, steps, speed):
        if device not in ("auto", "cpu", "cuda") or int(steps) not in (16, 32, 48) or not 0.3 <= float(speed) <= 2:
            raise ValueError("Configuração inválida.")
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA não está disponível nesta máquina. Escolha Automático ou CPU.")
        with self._storage:
            data = self.read()
            data["settings"] = {"device": device, "steps": int(steps), "speed": float(speed)}
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

    def checkpoints(self, project):
        p = self.project(project)
        return self.backend().get_checkpoints_project(p.name, False)[0]

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

    def register_model(self, name, checkpoint, vocabulary="", architecture="F5TTS_v1_Base"):
        name = (name or "").strip()
        if not name or len(name) > 100:
            raise ValueError("Dê ao modelo um nome de até 100 caracteres.")
        if architecture not in ("F5TTS_v1_Base", "F5TTS_Base", "E2TTS_Base"):
            raise ValueError("Escolha a mesma arquitetura usada no treinamento.")
        checkpoint = self.local_model_file(checkpoint, (".pt", ".pth", ".ckpt", ".safetensors"))
        vocabulary = self.local_model_file(vocabulary, (".txt",)) if vocabulary else ""
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
            data.setdefault("models", []).append(model)
            self.write(data)
        return model

    def generate(
        self, text, reference=None, transcript="", voice_id=None, project=None, checkpoint=None, model_id="original"
    ):
        if not text or not text.strip():
            raise ValueError("Escreva o texto que deseja transformar em voz.")
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação atual ou pare o treinamento antes de gerar áudio.")
        try:
            data = self.read()
            if voice_id:
                voice = next((v for v in data["voices"] if v["id"] == voice_id), None)
                if not voice:
                    raise ValueError("Selecione uma voz existente.")
                reference, transcript = voice["reference"], voice["transcript"]
            reference = self.validate_reference(reference or "", allow_external=bool(voice_id))
            vocabulary = ""
            architecture = "F5TTS_v1_Base"
            model_name = "F5-TTS original · inglês / chinês"
            if project:
                if model_id != "original":
                    raise ValueError("Escolha um modelo salvo ou um projeto treinado, sem combinar os dois.")
                if checkpoint not in self.checkpoints(project):
                    raise ValueError("Selecione um checkpoint do projeto.")
                training_options = self.training_options(project)
                vocabulary = training_options["vocabulary"] or str(self.project(project).path / "vocab.txt")
                architecture = training_options["architecture"]
                model_name = project
            elif checkpoint:
                raise ValueError("O checkpoint precisa pertencer a um projeto.")
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
            key = (
                checkpoint or "",
                vocabulary,
                architecture,
                settings["device"],
                version,
                self.model_version(vocabulary),
            )
            if self._model_key != key:
                self._model = None
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                factory = self._model_factory
                if factory is None:
                    from f5_tts.api import F5TTS

                    factory = F5TTS
                self._model = factory(
                    model=architecture,
                    ckpt_file=checkpoint or "",
                    vocab_file=vocabulary,
                    device=None if settings["device"] == "auto" else settings["device"],
                )
                self._model_key = key
            identifier = uuid4().hex
            path = self.outputs / (identifier + ".wav")
            spoken_text = self.pronounce(text.strip(), data.get("pronunciations", {}))
            self._model.infer(
                ref_file=reference,
                ref_text=transcript or "",
                gen_text=spoken_text,
                nfe_step=settings["steps"],
                speed=settings["speed"],
                seed=42,
                file_wave=str(path),
            )
            if not path.exists():
                raise RuntimeError("O modelo não produziu o arquivo de áudio.")
            entry = {
                "id": identifier,
                "text": text.strip(),
                "spoken_text": spoken_text,
                "path": str(path),
                "voice": voice_id,
                "project": project,
                "checkpoint": checkpoint,
                "model_version": version,
                "model_id": model_id if not project else None,
                "model_name": model_name,
                "architecture": architecture,
                "steps": settings["steps"],
                "speed": settings["speed"],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "rating": "",
                "note": "",
            }
            with self._storage:
                data = self.read()
                data["history"].insert(0, entry)
                self.write(data)
            return entry
        finally:
            self._operation.release()

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

    def import_audio(self, name, paths, language, consent):
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução antes de importar.")
        try:
            for path in paths:
                self.managed_upload(path)
            return import_ready_audio(self.project(name), paths, language, consent, self.backend())
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
            p.review(rows)
        finally:
            self._operation.release()

    def prepare(self, name):
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução antes de preparar.")
        try:
            p = self.project(name)
            # Reuse the exact preparation workflow already tested in the guided UI.
            backend = self.backend()
            callbacks = {block.fn.__name__: block.fn for block in backend.app.fns.values() if block.fn}
            callbacks["prepare"](p.name)
            return p.summary()
        finally:
            self._operation.release()

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
            "hardware": info,
        }

    def save_training_options(self, name, options):
        current = self.training_options(name)
        merged = {**current, **options}
        architectures = ("F5TTS_v1_Base", "F5TTS_Base", "E2TTS_Base")
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
        ):
            merged[field] = int(merged[field])
        if merged["base_checkpoint"]:
            merged["base_checkpoint"] = self.local_model_file(
                merged["base_checkpoint"], (".pt", ".pth", ".ckpt", ".safetensors")
            )
        if merged["vocabulary"]:
            merged["vocabulary"] = self.local_model_file(merged["vocabulary"], (".txt",))
        project = self.project(name)
        data = project.read()
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
        vocabulary = options["vocabulary"] or str(project.path / "vocab.txt")
        return self.register_model(name, checkpoint, vocabulary if Path(vocabulary).exists() else "", architecture)

    def start_training(self, name, options=None):
        project = self.project(name)
        previous_data = project.read()
        if options is None:
            options = self.training_options(name)
        elif isinstance(options, int):
            epochs = options
            options = self.training_options(name)
            options["epochs"] = epochs
            options = self.save_training_options(name, options)
        else:
            options = self.save_training_options(name, options)
        origin = {"architecture": options["architecture"], "base_checkpoint": options["base_checkpoint"]}
        if previous_data.get("training_origin") and previous_data["training_origin"] != origin:
            current_data = project.read()
            if "training_options" in previous_data:
                current_data["training_options"] = previous_data["training_options"]
            else:
                current_data.pop("training_options", None)
            project.write(current_data)
            raise ValueError(
                "Este projeto já tem checkpoints de outra arquitetura ou modelo inicial. Crie outro projeto para iniciar uma linha de treino diferente."
            )
        p = project
        data = p.read()
        if data.get("prepared") != p.fingerprint() or not (p.path / "raw.arrow").exists():
            raise ValueError("Revise e prepare os exemplos antes de treinar.")
        if not torch.cuda.is_available():
            raise ValueError("O treinamento guiado requer GPU NVIDIA/CUDA. A preparação funciona em CPU.")
        if not self._operation.acquire(blocking=False):
            raise ValueError("Já existe uma operação em execução.")
        if not data.get("training_origin"):
            data["training_origin"] = origin
            p.write(data)
        self._stop_requested.clear()
        self.job = {"running": True, "message": "Iniciando treinamento…", "project": p.name}

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
                    options["vocabulary"],
                    options["precision"],
                    False,
                    "none",
                    options["use_8bit_optimizer"],
                    options["num_workers"],
                ):
                    self.job["message"] = str(message)
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
        return dict(self.job)

    def stop_training(self):
        if not self.job["running"]:
            return "Não há treinamento em execução."
        self._stop_requested.set()
        if self._backend is None or self._backend.training_process is None:
            return "Cancelamento solicitado. Aguarde a preparação terminar."
        return self._backend.stop_training()[0]
