"""Exercise the guided dataset and real Gradio callback wiring without downloading models."""

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import soundfile as sf
import torch

from f5_tts.audio import save_audio
from f5_tts.train.guided_training import TrainingProject, import_ready_audio


class GuidedTrainingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.audio = self.root / "source.wav"
        wave = (torch.sin(torch.arange(120000) * 0.05) * 0.1).unsqueeze(0)
        save_audio(self.audio, wave, 24000)
        self.project = TrainingProject(self.root / "data", "test_voice")

    def tearDown(self):
        self.folder.cleanup()

    def populate(self):
        for text in ("Hello world.", "This is a recording.", "Another sentence."):
            self.project.add(self.audio, text, True)
        rows = self.project.rows()
        for row in rows:
            row[1] = True
        self.project.review(rows)

    def test_reopen_and_explicit_approval(self):
        self.project.add(self.audio, "Hello world.", True)
        reopened = TrainingProject(self.root / "data", "test_voice")
        self.assertEqual(len(reopened.rows()), 1)
        self.assertFalse(reopened.rows()[0][1])
        self.assertEqual(sf.info(reopened.path / "wavs" / (reopened.rows()[0][0] + ".wav")).samplerate, 24000)

    def test_invalid_samples_and_names(self):
        with self.assertRaises(ValueError):
            TrainingProject(self.root, "../escape")
        with self.assertRaises(ValueError):
            self.project.add(self.audio, "Hello world.", False)
        with self.assertRaises(ValueError):
            self.project.add(self.audio, "Hello|world", True)
        save_audio(self.root / "silence.wav", torch.zeros(1, 24000), 24000)
        with self.assertRaises(ValueError):
            self.project.add(self.root / "silence.wav", "Hello world.", True)

    def test_review_rejects_forged_ids(self):
        self.project.add(self.audio, "Hello world.", True)
        with self.assertRaises(ValueError):
            self.project.review([["../file", True, "Hello world.", 5]])

    def test_export_only_approved_and_invalidate(self):
        self.populate()
        self.project.export_reviewed()
        self.assertEqual(len((self.project.path / "metadata.csv").read_text(encoding="utf-8-sig").splitlines()), 3)
        data = self.project.read()
        data["prepared"] = self.project.fingerprint()
        self.project.write(data)
        (self.project.path / "raw.arrow").write_text("old")
        self.project.add(self.audio, "New sentence.", True)
        self.assertNotIn("prepared", self.project.read())
        self.assertFalse((self.project.path / "raw.arrow").exists())

    def test_feedback_requires_a_result_and_persists(self):
        with self.assertRaises(ValueError):
            self.project.feedback("checkpoint", None, "Ficou bom", "")
        self.project.feedback("checkpoint", self.audio, "Precisa melhorar", "pronunciation")
        self.assertEqual(len(self.project.read()["feedback"]), 1)

    def test_batch_upload_transcribes_and_preserves_examples(self):
        self.project.add(self.audio, "Existing example.", True)
        wave = (torch.sin(torch.arange(31 * 24000) * 0.05) * 0.1).numpy()
        backend = SimpleNamespace(
            librosa=SimpleNamespace(load=lambda *args, **kwargs: (wave, 24000)),
            Slicer=lambda rate: SimpleNamespace(slice=lambda audio: [(audio, 0, len(audio))]),
            transcribe=Mock(return_value="Texto reconhecido."),
        )
        message = import_ready_audio(self.project, [self.audio], "pt", True, backend)
        self.assertIn("2 trechos", message)
        self.assertEqual(len(self.project.rows()), 3)
        self.assertFalse(any(row[1] for row in self.project.rows()))
        self.assertEqual(backend.transcribe.call_count, 2)
        self.assertEqual(backend.transcribe.call_args.args[1], "pt")
        self.assertTrue(all(row[3] <= 30 for row in self.project.rows()))

    def test_failed_upload_keeps_previous_examples(self):
        self.project.add(self.audio, "Existing example.", True)
        backend = SimpleNamespace(librosa=SimpleNamespace(load=Mock(side_effect=ValueError("Invalid file"))))
        message = import_ready_audio(self.project, [self.audio], "pt", True, backend)
        self.assertIn("problemas", message)
        self.assertEqual(len(self.project.rows()), 1)

    def test_real_prepare_callback_and_train_dispatch(self):
        from f5_tts.train import finetune_gradio as backend

        self.populate()
        source_vocab = Path(__file__).resolve().parents[1] / "data/Emilia_ZH_EN_pinyin/vocab.txt"
        target = self.root / "data/Emilia_ZH_EN_pinyin"
        target.mkdir(parents=True)
        shutil.copy(source_vocab, target / "vocab.txt")
        callbacks = {block.fn.__name__: block.fn for block in backend.app.fns.values() if block.fn}
        with patch.object(backend, "path_data", str(self.root / "data")):
            callbacks["prepare"](self.project.name)
            self.assertTrue((self.project.path / "raw.arrow").exists())
            self.assertEqual(self.project.read()["prepared"], self.project.fingerprint())
            with (
                patch("torch.cuda.is_available", return_value=True),
                patch.object(backend, "start_training", return_value=iter([("done", {}, {})])) as launch,
            ):
                result = list(callbacks["train"](self.project.name, "Teste rápido · 5 passagens"))
                self.assertEqual(result[0][0], "done")
                self.assertEqual(launch.call_args.args[8], 5)
                self.assertTrue(launch.call_args.args[13])
            self.project.add(self.audio, "Another example.", True)
            with self.assertRaises(Exception):
                list(callbacks["train"](self.project.name, "Teste rápido · 5 passagens"))


if __name__ == "__main__":
    unittest.main()
