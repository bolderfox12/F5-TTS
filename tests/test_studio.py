"""Studio service tests use synthetic audio and a fake model, never model downloads."""

import base64
import http.client
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch

from f5_tts.audio import save_audio
from f5_tts.studio.service import StudioService


class FakeModel:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.last = None

    def infer(self, **kwargs):
        self.last = kwargs
        save_audio(kwargs["file_wave"], torch.ones(1, 24000) * 0.1, 24000)


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.service = StudioService(self.root / "studio", self.root / "data", model_factory=FakeModel)
        self.reference = self.service.upload_path("voice.wav")
        save_audio(self.reference, torch.ones(1, 24000 * 2) * 0.1, 24000)

    def tearDown(self):
        self.folder.cleanup()

    def test_lazy_startup_and_voice_persistence(self):
        self.assertIsNone(self.service._model)
        voice = self.service.save_voice("Minha voz", self.reference, "Hello world")
        reopened = StudioService(self.root / "studio", self.root / "data")
        self.assertEqual(reopened.read()["voices"][0]["id"], voice["id"])

    def test_generation_uses_settings_and_history_survives(self):
        voice = self.service.save_voice("Narrador", self.reference, "Hello world")
        self.service.settings("cpu", 48, 0.9)
        entry = self.service.generate("A new sentence.", voice_id=voice["id"])
        self.assertTrue(Path(entry["path"]).exists())
        self.assertEqual(self.service._model.options["device"], "cpu")
        self.assertEqual(self.service._model.last["nfe_step"], 48)
        self.assertEqual(self.service._model.last["speed"], 0.9)
        self.assertEqual(self.service.read()["history"][0]["text"], "A new sentence.")
        self.service.rate(entry["id"], "Precisa melhorar", "Pronúncia")
        self.assertEqual(self.service.read()["history"][0]["note"], "Pronúncia")

    def test_generation_can_use_a_trained_checkpoint(self):
        project = self.service.project("trained_voice")
        vocabulary = project.path / "vocab.txt"
        vocabulary.write_text("a\nb\n")
        checkpoint = str(self.root / "model_last.pt")
        self.service._backend = SimpleNamespace(get_checkpoints_project=lambda *args: ([checkpoint], checkpoint))
        result = self.service.generate("New narration", self.reference, project=project.name, checkpoint=checkpoint)
        self.assertEqual(self.service._model.options["ckpt_file"], checkpoint)
        self.assertEqual(self.service._model.options["vocab_file"], str(vocabulary))
        self.assertEqual(result["checkpoint"], checkpoint)
        with self.assertRaises(ValueError):
            self.service.generate("New narration", self.reference, project=project.name, checkpoint="unregistered.pt")

    def test_invalid_inputs_never_load_a_model(self):
        with self.assertRaises(ValueError):
            self.service.generate("", self.reference)
        with self.assertRaises(ValueError):
            self.service.generate("Hello", "/etc/passwd")
        with self.assertRaises(ValueError):
            self.service.upload_path("file.exe")
        with self.assertRaises(ValueError):
            self.service.project("../escape")
        self.assertIsNone(self.service._model)
        self.assertFalse(self.service._operation.locked())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_microphone_recording_becomes_a_managed_reference(self):
        encoded = base64.b64encode(self.reference.read_bytes()).decode("ascii")
        path = self.service.microphone_reference(encoded)
        self.assertNotEqual(path, str(self.reference))
        self.assertEqual(self.service.validate_reference(path), path)
        voice = self.service.save_voice("Microfone", path, "Hello world")
        result = self.service.generate("New narration", voice_id=voice["id"])
        self.assertTrue(Path(result["path"]).exists())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_invalid_microphone_recordings_leave_no_partial_files(self):
        before = set(self.service.uploads.iterdir())
        for encoded in (None, "", "!not-base64!", "x" * 800001, base64.b64encode(b"not audio").decode()):
            with self.subTest(encoded=str(encoded)[:20]), self.assertRaises(ValueError):
                self.service.microphone_reference(encoded)
        short = self.service.upload_path("too-short.wav")
        save_audio(short, torch.zeros(1, 2400), 24000)
        before.add(short)
        with self.assertRaises(ValueError):
            self.service.microphone_reference(base64.b64encode(short.read_bytes()).decode())
        self.assertEqual(set(self.service.uploads.iterdir()), before)

    def test_microphone_reports_missing_ffmpeg(self):
        with patch("f5_tts.studio.service.shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "FFmpeg"):
                self.service.microphone_reference(base64.b64encode(b"audio").decode())

    def test_saved_models_switch_without_replacing_original(self):
        checkpoint = self.root / "portugues.pt"
        checkpoint.write_bytes(b"fake checkpoint for orchestration")
        vocabulary = self.root / "vocab.txt"
        vocabulary.write_text("a\nb\n")
        model = self.service.register_model("Português", checkpoint, vocabulary, "F5TTS_Base")
        reopened = StudioService(self.service.home, self.service.data_root)
        self.assertEqual(reopened.models()[1], model)
        original = self.service.models()[0].copy()
        portuguese = self.service.generate("Olá", self.reference, "Hello", model_id=model["id"])
        first_model = self.service._model
        self.assertEqual(first_model.options["ckpt_file"], str(checkpoint))
        self.assertEqual(first_model.options["vocab_file"], str(vocabulary))
        self.assertEqual(first_model.options["model"], "F5TTS_Base")
        self.assertEqual(portuguese["model_name"], "Português")
        self.service.generate("Outra frase", self.reference, "Hello", model_id=model["id"])
        self.assertIs(self.service._model, first_model)
        english = self.service.generate("Hello", self.reference, "Hello", model_id="original")
        self.assertIsNot(self.service._model, first_model)
        self.assertEqual(self.service._model.options["ckpt_file"], "")
        self.assertEqual(self.service._model.options["model"], "F5TTS_v1_Base")
        self.assertEqual(english["model_id"], "original")
        self.assertEqual(self.service.models()[0], original)
        self.assertTrue(checkpoint.exists())

    def test_saved_models_validate_files_and_missing_selection(self):
        with self.assertRaises(ValueError):
            self.service.register_model("Português", self.root / "missing.pt")
        with self.assertRaises(ValueError):
            self.service.generate("Olá", self.reference, model_id="missing")
        checkpoint = self.root / "portugues.pt"
        checkpoint.write_bytes(b"fake")
        registered = self.service.register_model("Português", checkpoint)
        with self.assertRaises(ValueError):
            self.service.register_model("português", checkpoint)
        with self.assertRaises(ValueError):
            self.service.register_model("Outro", checkpoint, architecture="unknown")
        checkpoint.unlink()
        with self.assertRaises(ValueError):
            self.service.generate("Olá", self.reference, model_id=registered["id"])
        self.assertIsNone(self.service._model)
        self.assertFalse(self.service._operation.locked())

    def test_exclusive_operation_and_cuda_validation(self):
        self.service._operation.acquire()
        try:
            with self.assertRaises(ValueError):
                self.service.generate("Hello", self.reference)
            with self.assertRaises(ValueError):
                self.service.import_audio("test", [], "pt", True)
        finally:
            self.service._operation.release()
        with patch("torch.cuda.is_available", return_value=False):
            with self.assertRaises(ValueError):
                self.service.settings("cuda", 32, 1)
            self.assertFalse(self.service.hardware()["cuda"])

    def test_training_requires_current_preparation(self):
        with self.assertRaises(ValueError):
            self.service.start_training("test", 5)
        self.assertFalse(self.service.job["running"])
        self.assertIn("Não há", self.service.stop_training())

    def test_training_stop_drains_backend_and_releases_lock(self):
        project = self.service.project("training_test")
        data = project.read()
        data["prepared"] = project.fingerprint()
        project.write(data)
        (project.path / "raw.arrow").write_bytes(b"test")
        entered = threading.Event()
        backend = SimpleNamespace(training_process=None, stop_signal=False)

        def train(*args):
            backend.training_process = object()
            entered.set()
            yield "running", {}, {}
            while not backend.stop_signal:
                time.sleep(0.01)
            yield "stopping", {}, {}
            backend.training_process = None
            yield "stopped", {}, {}

        def stop():
            backend.stop_signal = True
            return "stop requested", {}, {}

        backend.start_training, backend.stop_training = train, stop
        self.service._backend = backend
        with patch("torch.cuda.is_available", return_value=True), patch("torch.cuda.empty_cache"):
            self.service.start_training(project.name, 5)
            self.assertTrue(entered.wait(timeout=2))
            self.service.stop_training()
            for _ in range(200):
                if not self.service.job["running"]:
                    break
                time.sleep(0.01)
        self.assertFalse(self.service.job["running"])
        self.assertIsNone(backend.training_process)
        self.assertFalse(self.service._operation.locked())


@unittest.skipUnless(importlib.util.find_spec("nicegui"), "Install .[studio] to exercise NiceGUI routes")
class StudioRouteTests(unittest.TestCase):
    def test_five_real_pages_without_model_downloads(self):
        with tempfile.TemporaryDirectory() as folder:
            # Bind inside a child process so ui.run has its normal lifecycle.
            entry = (
                "import os; [os.environ.pop(k) for k in list(os.environ) if k.lower().endswith('_proxy')]; "
                "os.environ['GRADIO_ANALYTICS_ENABLED']='False'; "
                "from f5_tts.studio.app import main; main()"
            )
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
            # This child is a normal server, not a NiceGUI screen-test worker.
            env.pop("PYTEST_CURRENT_TEST", None)
            startup_log = Path(folder) / "startup.log"
            with startup_log.open("w") as log:
                process = subprocess.Popen(
                    [sys.executable, "-c", entry, "--no-browser", "--port", "17862", "--workspace", folder],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            try:
                # Cold imports on Windows-mounted WSL disks can exceed ten seconds.
                timeout = float(os.environ.get("F5_STUDIO_TEST_STARTUP_TIMEOUT", "120"))
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        connection = http.client.HTTPConnection("127.0.0.1", 17862, timeout=2)
                        connection.request("GET", "/")
                        response = connection.getresponse()
                        body = response.read().decode()
                        connection.close()
                        break
                    except (ConnectionError, OSError):
                        if process.poll() is not None:
                            self.fail("Studio server exited before startup:\n" + startup_log.read_text())
                        time.sleep(0.1)
                else:
                    self.fail("Studio server did not start:\n" + startup_log.read_text())
                self.assertEqual(response.status, 200)
                self.assertIn("Transforme texto em voz", body)
                for route, expected in [
                    ("/voices", "Suas vozes"),
                    ("/models", "Seus modelos"),
                    ("/training", "Enviar seus"),
                    ("/history", "Tudo o que"),
                    ("/settings", "Controle o"),
                    ("/projects", "Histórias com"),
                    ("/transcription", "Do áudio"),
                    ("/revoice", "Outra voz"),
                    ("/audio-tools", "Prepare e exporte"),
                ]:
                    connection = http.client.HTTPConnection("127.0.0.1", 17862, timeout=10)
                    connection.request("GET", route)
                    response = connection.getresponse()
                    body = response.read().decode()
                    connection.close()
                    self.assertEqual(response.status, 200, route)
                    self.assertIn(expected, body)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
