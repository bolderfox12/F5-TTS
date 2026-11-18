"""Offline coverage for narration, transcription, translation and real FFmpeg processing."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import soundfile as sf
import torch

from f5_tts.audio import save_audio
from f5_tts.studio.local_tools import srt_time
from f5_tts.studio.service import StudioService


class FakeModel:
    def __init__(self, **kwargs):
        pass

    def infer(self, **kwargs):
        save_audio(kwargs["file_wave"], torch.ones(1, 24000) * 0.1, 24000)


class LocalToolsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.service = StudioService(self.root / "studio", self.root / "data", model_factory=FakeModel)
        self.reference = self.service.upload_path("reference.wav")
        save_audio(self.reference, torch.sin(torch.arange(48000) * 0.1).unsqueeze(0) * 0.1, 24000)
        self.voice = self.service.save_voice("Narrador", self.reference, "Hello world")
        self.second = self.service.save_voice("Outra voz", self.reference, "Another sentence")

    def tearDown(self):
        self.folder.cleanup()

    def test_narration_mixed_voices_export_and_stale_detection(self):
        project = self.service.create_narration("História", "First paragraph.\n\nSecond paragraph.", self.voice["id"])
        second = project["blocks"][1]
        self.service.edit_block(project["id"], second["id"], "Another paragraph.", self.second["id"], 0.5)
        result = self.service.render_narration(project["id"])
        self.assertAlmostEqual(sf.info(result["path"]).duration, 2.4, places=2)
        self.assertEqual(result["segments"][1]["start"], 1.4)
        self.assertIn("00:00:01,400", Path(result["srt"]).read_text())
        first = project["blocks"][0]
        self.service.edit_block(project["id"], first["id"], "Changed paragraph.", self.voice["id"], 0.4)
        with self.assertRaises(ValueError):
            self.service.export_narration(project["id"])
        self.service.render_block(project["id"], first["id"])
        self.assertTrue(Path(self.service.export_narration(project["id"])["path"]).exists())
        reopened = StudioService(self.root / "studio", self.root / "data")
        self.assertEqual(len(reopened.narrations()), 1)

    def test_stop_render_preserves_finished_clips_and_can_resume(self):
        project = self.service.create_narration("Stop", "First.\n\nSecond.", self.voice["id"])
        generate = self.service.generate
        count = 0

        def stop_after_first(*args, **kwargs):
            nonlocal count
            result = generate(*args, **kwargs)
            count += 1
            if count == 1:
                self.service.stop_render()
            return result

        self.service.generate = stop_after_first
        with self.assertRaises(ValueError):
            self.service.render_narration(project["id"])
        current = self.service.narration(project["id"])
        self.assertTrue(self.service.block_ready(current["blocks"][0]))
        self.assertFalse(self.service.block_ready(current["blocks"][1]))
        self.assertTrue(Path(self.service.render_narration(project["id"])["path"]).exists())

    def test_reorder_and_validation(self):
        p = self.service.create_narration("Order", "One.\n\nTwo.", self.voice["id"])
        second = p["blocks"][1]["id"]
        self.service.move_block(p["id"], second, -1)
        self.assertEqual(self.service.narration(p["id"])["blocks"][0]["id"], second)
        with self.assertRaises(ValueError):
            self.service.edit_block(p["id"], second, "Text", self.voice["id"], -1)
        with self.assertRaises(ValueError):
            self.service.create_narration("Title", "", self.voice["id"])
        self.assertEqual(srt_time(3601.25), "01:00:01,250")

    def test_transcription_exports_and_correction(self):
        self.service._asr_factory = Mock(
            return_value={
                "text": "Hello world",
                "chunks": [{"text": "Hello", "timestamp": (0.0, 1.0)}, {"text": "world", "timestamp": (1.0, None)}],
            }
        )
        record = self.service.transcribe_audio(self.reference, "en")
        self.assertEqual(record["text"], "Hello world")
        self.assertEqual(record["segments"][-1]["end"], 2.0)
        self.assertTrue(Path(record["srt"]).exists())
        updated = self.service.edit_transcript(record["id"], 1, "everyone")
        self.assertIn("everyone", Path(updated["srt"]).read_text())
        self.assertEqual(Path(updated["txt"]).read_text(), "Hello everyone")
        self.assertFalse(self.service._operation.locked())

    def test_trimmed_reference_can_be_transcribed_without_saving_a_transcript(self):
        clipped = self.service.trim_reference(self.reference, 0.25, 1.5)
        self.assertAlmostEqual(clipped["duration"], 1.25)
        self.assertTrue(Path(self.reference).exists())
        self.service._asr_factory = Mock(return_value={"text": "Selected words"})
        self.assertEqual(self.service.transcribe_reference(clipped["path"]), "Selected words")
        self.assertEqual(self.service.transcripts(), [])
        self.assertNotIn("language", self.service._asr_factory.call_args.kwargs["generate_kwargs"])
        with self.assertRaises(ValueError):
            self.service.trim_reference(self.reference, 0, 0.25)

    def test_translation_keeps_original_and_subtitle_times(self):
        original = self.service.store_transcript(
            {
                "source": str(self.reference),
                "name": "English",
                "language": "en",
                "segments": [{"start": 0, "end": 2, "text": "Hello"}],
            }
        )
        self.service._translation_factory = lambda text: "Olá"
        translated = self.service.translate_transcript(original["id"], "en", "pt")
        self.assertEqual(translated["text"], "Olá")
        self.assertEqual(translated["segments"][0]["end"], 2)
        self.assertEqual(self.service.transcript(original["id"])["text"], "Hello")
        self.service.set_offline(True)
        self.assertTrue(self.service.read()["offline"])
        with self.assertRaises(ValueError):
            self.service.translate_transcript(original["id"], "en", "en")

    def test_pronunciation_is_applied_without_changing_script(self):
        self.service.set_pronunciations("IA = inteligência artificial")
        entry = self.service.generate("A IA funciona. A palavra praia permanece.", self.reference)
        self.assertIn("inteligência artificial", entry["spoken_text"])
        self.assertIn("praia", entry["spoken_text"])
        self.assertIn("IA", entry["text"])
        project = self.service.create_narration("Pronúncia", "A IA funciona.", self.voice["id"])
        self.service.render_block(project["id"], project["blocks"][0]["id"])
        self.service.set_pronunciations("IA = outra leitura")
        self.assertFalse(self.service.block_ready(self.service.narration(project["id"])["blocks"][0]))
        with self.assertRaises(ValueError):
            self.service.set_pronunciations("IA = leitura\nia = duplicada")

    def test_project_can_select_and_clear_trained_model(self):
        project = self.service.create_narration("Treinado", "First paragraph.", self.voice["id"])
        from types import SimpleNamespace

        checkpoint = str(self.root / "model.pt")
        Path(checkpoint).write_bytes(b"version one")
        trained = self.service.project("voice_model")
        (trained.path / "vocab.txt").write_text("a\nb\n")
        self.service._backend = SimpleNamespace(get_checkpoints_project=lambda *args: ([checkpoint], checkpoint))
        self.service.set_narration_model(project["id"], trained.name, checkpoint)
        self.service.render_block(project["id"], project["blocks"][0]["id"])
        rendered = self.service.narration(project["id"])["blocks"][0]
        self.assertTrue(self.service.block_ready(rendered))
        Path(checkpoint).write_bytes(b"version two, updated")
        self.assertFalse(self.service.block_ready(rendered))
        self.service.set_narration_model(project["id"])
        self.assertIsNone(self.service.narration(project["id"])["blocks"][0]["checkpoint"])

    def test_model_failure_releases_operation(self):
        self.service._asr_factory = Mock(side_effect=RuntimeError("no model"))
        with self.assertRaises(RuntimeError):
            self.service.transcribe_audio(self.reference)
        self.assertFalse(self.service._operation.locked())
        with self.assertRaises(ValueError):
            self.service.transcribe_audio(self.reference, model_id="untrusted/model")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_real_ffmpeg_processing_and_mp3_conversion(self):
        result = self.service.process_audio(self.reference, "normalize", "wav")
        self.assertTrue(sf.info(result).duration > 0)
        mp3 = self.service.process_audio(self.reference, "none", "mp3")
        self.assertGreater(Path(mp3).stat().st_size, 100)
        self.assertEqual(len(self.service.read()["processed_audio"]), 2)
        with self.assertRaises(ValueError):
            self.service.process_audio(self.reference, "arbitrary_filter")


if __name__ == "__main__":
    unittest.main()
