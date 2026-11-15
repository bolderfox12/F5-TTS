"""Persistent, single-user workspace. Models are loaded only when requested."""

import gc
import json
import threading
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import soundfile as sf
import torch

from f5_tts.train.guided_training import TrainingProject, import_ready_audio


class StudioService:
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
        self._stop_requested = threading.Event()
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
        return self.uploads / (uuid4().hex + suffix)

    def managed_upload(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.uploads) or not path.is_file():
            raise ValueError("Envie uma gravação pelo aplicativo primeiro.")
        return path

    def validate_reference(self, path):
        path = self.managed_upload(path)
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

    def checkpoints(self, project):
        p = self.project(project)
        return self.backend().get_checkpoints_project(p.name, False)[0]

    def generate(self, text, reference=None, transcript="", voice_id=None, project=None, checkpoint=None):
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
            reference = self.validate_reference(reference or "")
            vocabulary = ""
            if project:
                if checkpoint not in self.checkpoints(project):
                    raise ValueError("Selecione um checkpoint do projeto.")
                vocabulary = str(self.project(project).path / "vocab.txt")
            elif checkpoint:
                raise ValueError("O checkpoint precisa pertencer a um projeto.")
            settings = data["settings"]
            key = (checkpoint or "", vocabulary, settings["device"])
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
                    ckpt_file=checkpoint or "",
                    vocab_file=vocabulary,
                    device=None if settings["device"] == "auto" else settings["device"],
                )
                self._model_key = key
            identifier = uuid4().hex
            path = self.outputs / (identifier + ".wav")
            self._model.infer(
                ref_file=reference,
                ref_text=transcript or "",
                gen_text=text.strip(),
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
                "path": str(path),
                "voice": voice_id,
                "project": project,
                "checkpoint": checkpoint,
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

    def start_training(self, name, epochs):
        if int(epochs) not in (5, 20, 50):
            raise ValueError("Escolha um perfil de treinamento válido.")
        p = self.project(name)
        data = p.read()
        if data.get("prepared") != p.fingerprint() or not (p.path / "raw.arrow").exists():
            raise ValueError("Revise e prepare os exemplos antes de treinar.")
        if not torch.cuda.is_available():
            raise ValueError("O treinamento guiado requer GPU NVIDIA/CUDA. A preparação funciona em CPU.")
        if not self._operation.acquire(blocking=False):
            raise ValueError("Já existe uma operação em execução.")
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
                    "F5TTS_v1_Base",
                    1e-5,
                    800,
                    "frame",
                    1,
                    4,
                    1.0,
                    int(epochs),
                    0,
                    100,
                    3,
                    100,
                    True,
                    "",
                    "pinyin",
                    "",
                    "fp16",
                    False,
                    "none",
                    False,
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

    def stop_training(self):
        if not self.job["running"]:
            return "Não há treinamento em execução."
        self._stop_requested.set()
        if self._backend is None or self._backend.training_process is None:
            return "Cancelamento solicitado. Aguarde a preparação terminar."
        return self._backend.stop_training()[0]
