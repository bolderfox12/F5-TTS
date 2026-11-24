"""Offline coverage for narration, transcription, translation and real FFmpeg processing."""

import base64
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import numpy as np
import soundfile as sf
import torch

from f5_tts.audio import save_audio
from f5_tts.studio import chatterbox_backend as chatterbox
from f5_tts.studio.local_tools import srt_time
from f5_tts.studio.service import StudioService
from f5_tts.studio.video_dubbing import (
    GEMINI_TRANSCRIPTION,
    _assign_speakers,
    analyze_video,
    dub_video,
    speaker_references,
    translate_dialogue,
)
from f5_tts.studio.video_editor import edit_dubbed_video
from f5_tts.studio.video_probe import video_duration


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
        with self.assertRaises(ValueError):
            self.service.transcribe_audio(self.reference, model_id="untrusted/model")

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

    def test_gemini_translation_preserves_ids_and_only_sends_dialogue(self):
        rows = [
            {"start": 0, "end": 1, "text": "Olá", "speaker": 1, "reference": Path("/tmp/voice.wav")},
            {"start": 1, "end": 2, "text": "Bom dia", "speaker": 2},
        ]
        self.service.save_review_key("gemini", "test-key")
        with patch("f5_tts.studio.script_review.httpx.post") as request:
            reply = {"lines": [{"id": 1, "text": "Good morning"}, {"id": 0, "text": "Hello"}]}
            candidate = {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(reply)}]}}
            request.return_value.json.return_value = {"candidates": [candidate]}
            translated = translate_dialogue(self.service, rows, "pt", "en", provider="gemini")
            self.assertEqual(translated, ["Hello", "Good morning"])
            parts = request.call_args.kwargs["json"]["contents"][0]["parts"]
            self.assertEqual(json.loads(parts[0]["text"]), [{"id": 0, "text": "Olá"}, {"id": 1, "text": "Bom dia"}])
            reply["lines"][0]["id"] = 0
            candidate["content"]["parts"][0]["text"] = json.dumps(reply)
            with self.assertRaisesRegex(ValueError, "para cada fala"):
                translate_dialogue(self.service, rows, "pt", "en", provider="gemini")
        self.service.set_offline(True)
        with self.assertRaisesRegex(ValueError, "modo offline"):
            translate_dialogue(self.service, rows, "pt", "en", provider="gemini")

    def test_shorten_dubbing_uses_measured_duration_and_returns_shorter_alternatives(self):
        from f5_tts.studio.script_review import shorten_dubbing_line

        self.service.save_review_key("gemini", "test-key")
        with patch("f5_tts.studio.script_review.httpx.post") as request:
            candidates = {"alternatives": ["Come now!", "Come now!", "Please come as soon as you possibly can!"]}
            request.return_value.json.return_value = {
                "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(candidates)}]}}]
            }
            variants = shorten_dubbing_line(
                self.service, "Venha agora, por favor!", "Please come right now!", "en", 1.4, 2.8, context=["I'm here."]
            )
            self.assertEqual(variants, ["Come now!"])
            parts = request.call_args.kwargs["json"]["contents"][0]["parts"]
            payload = json.loads(parts[0]["text"])
            self.assertEqual(payload["availableSeconds"], 1.4)
            self.assertEqual(payload["currentAudioSeconds"], 2.8)
            self.assertEqual(payload["targetLettersAndNumbers"], 8)
            self.assertEqual(payload["adjacentTranslations"], ["I'm here."])

    @unittest.skipUnless(shutil.which("ffmpeg"), "requires FFmpeg")
    def test_gemini_translation_can_use_original_audio_and_reviewed_timing(self):
        source = self.service.uploads / "original.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=2", str(source)],
            check=True,
        )
        rows = [{"start": 0.25, "end": 1.75, "text": "Bom dia", "speaker": 2, "reference": Path("/tmp/ref.wav")}]
        with patch("f5_tts.studio.script_review.httpx.post") as request:
            request.return_value.json.return_value = {
                "candidates": [
                    {"finishReason": "STOP", "content": {"parts": [{"text": '{"lines":[{"id":0,"text":"Morning"}]}'}]}}
                ]
            }
            self.assertEqual(
                translate_dialogue(
                    self.service, rows, "pt", "en", provider="gemini", gemini_key="test-key", timing_source=source
                ),
                ["Morning"],
            )
        body = request.call_args.kwargs["json"]
        metadata, audio = body["contents"][0]["parts"]
        self.assertEqual(
            json.loads(metadata["text"]),
            [{"id": 0, "text": "Bom dia", "speaker": 2, "startSeconds": 0.25, "endSeconds": 1.75}],
        )
        self.assertEqual(audio["inlineData"]["mimeType"], "audio/mp3")
        self.assertGreater(len(base64.b64decode(audio["inlineData"]["data"], validate=True)), 100)
        self.assertIn("internal pauses", body["systemInstruction"]["parts"][0]["text"])

    def test_whisper_recovers_missing_speech_at_original_timestamp(self):
        audio = self.service.upload_path("with-gap.wav")
        samples = np.zeros(16000 * 4, dtype="float32")
        samples[32000:40000] = np.sin(np.arange(8000) * 0.1) * 0.04
        sf.write(audio, samples, 16000)
        self.service._asr_factory = Mock(
            side_effect=[
                {"chunks": [{"text": "Primeira fala", "timestamp": (0, 1)}]},
                {"chunks": [{"text": "Fala discreta", "timestamp": (0.15, 0.65)}]},
            ]
        )
        rows = self.service._transcribe_audio(
            str(audio),
            "pt",
            "openai/whisper-small",
            save_record=False,
            return_segments=True,
            recover_missing=True,
        )
        self.assertEqual([row["text"] for row in rows], ["Primeira fala", "Fala discreta"])
        self.assertAlmostEqual(rows[1]["start"], 2, delta=0.1)
        self.assertTrue(rows[1]["recovered"])
        self.assertEqual(self.service._asr_factory.call_count, 2)
        initial, retry = self.service._asr_factory.call_args_list
        self.assertNotIn("no_speech_threshold", initial.kwargs["generate_kwargs"])
        self.assertEqual(retry.kwargs["generate_kwargs"]["no_speech_threshold"], 0.8)
        self.assertEqual(retry.kwargs["generate_kwargs"]["logprob_threshold"], -1.0)
        self.assertEqual(retry.kwargs["generate_kwargs"]["temperature"], (0.0,))

    def test_dubbing_transcription_groups_words_into_short_lines(self):
        source = self.service.upload_path("dialogue.wav")
        sf.write(source, np.zeros(16000 * 9, dtype=np.float32), 16000)
        chunks = [{"text": f" palavra{i}", "timestamp": (i * 0.4, i * 0.4 + 0.3)} for i in range(18)]
        self.service._asr_factory = Mock(return_value={"chunks": chunks})
        rows = self.service._transcribe_audio(
            str(source), "pt", "openai/whisper-small", save_record=False, return_segments=True, short_lines=True
        )
        self.assertEqual(self.service._asr_factory.call_args.kwargs["return_timestamps"], "word")
        self.assertEqual(" ".join(row["text"] for row in rows).split(), [chunk["text"].strip() for chunk in chunks])
        self.assertTrue(all(row["end"] - row["start"] <= 5 for row in rows))
        self.assertGreater(len(rows), 1)

    def test_dubbing_transcription_drops_duplicate_timestamps_and_rechecks_loops(self):
        source = self.service.upload_path("dialogue.wav")
        sf.write(source, np.ones(16000 * 5, dtype=np.float32) * 0.02, 16000)
        chunks = [{"text": " agora", "timestamp": (i * 0.4, i * 0.4 + 0.3)} for i in range(8)]
        chunks.insert(1, chunks[0])
        self.service._asr_factory = Mock(
            side_effect=[
                {"chunks": chunks},
                {"chunks": [{"text": " agora", "timestamp": (0.1, 0.4)}]},
            ]
        )
        rows = self.service._transcribe_audio(
            str(source), "pt", "openai/whisper-small", save_record=False, return_segments=True, short_lines=True
        )
        self.assertEqual(rows, [{"start": 0.1, "end": 0.4, "text": "agora"}])
        self.assertEqual(self.service._asr_factory.call_count, 2)

    def test_dubbing_review_preserves_same_words_spoken_at_different_times(self):
        from f5_tts.studio.dubbing_transcript import transcript_warnings

        rows = [
            {"start": 0, "end": 1, "text": "Vou voltar amanhã."},
            {"start": 0.08, "end": 1.1, "text": "Vou voltar amanhã"},
            {"start": 4, "end": 5, "text": "Vou voltar amanhã."},
        ]
        self.assertEqual(transcript_warnings(rows), ["", "duplicate", ""])

    def test_auto_fit_uses_first_alternative_that_actually_fits(self):
        from f5_tts.studio.dubbing_review import DubbingProject, ExportRequest

        source = self.service.uploads / "draft.mp4"
        source.touch()
        project = DubbingProject(
            self.service,
            source,
            [
                {"start": 0, "end": 1, "speaker": 1, "text": "Frase longa"},
                {"start": 1, "end": 2, "speaker": 1, "text": "Sim"},
            ],
            2,
        )
        identifiers = list(project.rows)
        request = ExportRequest(
            lines=[
                {
                    "id": identifiers[0],
                    "language": "en",
                    "speaker": 1,
                    "start": 0,
                    "end": 1,
                    "text": "A much longer line",
                },
                {"id": identifiers[1], "language": "en", "speaker": 1, "start": 1, "end": 2, "text": "Yes"},
            ],
            auto_speed=False,
        )

        def measure(lines, **options):
            lengths = {"A much longer line": 2, "Still too long": 1.1, "Short": 0.9, "Yes": 0.5}
            return [(source, lengths[line.text]) for line in lines]

        with (
            patch.object(project, "prepare", side_effect=measure),
            patch(
                "f5_tts.studio.dubbing_review.shorten_dubbing_line", return_value=["Still too long", "Short"]
            ) as rewrite,
        ):
            fitted = project.fit_preview(request, {identifiers[0]: "Frase longa"}, "test-key")
        self.assertEqual([line.text for line in fitted.lines], ["Short", "Yes"])
        self.assertEqual(request.lines[0].text, "A much longer line")
        self.assertEqual(rewrite.call_count, 1)

    def test_model_failure_releases_operation(self):
        self.service._asr_factory = Mock(side_effect=RuntimeError("no model"))
        with self.assertRaises(RuntimeError):
            self.service.transcribe_audio(self.reference)
        self.assertFalse(self.service._operation.locked())

    def test_gemini_tts_keeps_voice_and_direction_in_preview_cache(self):
        from f5_tts.studio.dubbing_review import DubbingProject, ReviewLine
        from f5_tts.studio.gemini_tts import MODEL_ID

        source = self.service.uploads / "source.mp4"
        source.touch()
        row = {"start": 0, "end": 2, "speaker": 1, "text": "Olá!"}
        project = DubbingProject(self.service, source, [row], 2)
        line = ReviewLine(
            **row,
            id=next(iter(project.rows)),
            language="pt",
            model=MODEL_ID,
            gemini_voice="Charon",
            gemini_style="Calmo",
        )

        def synthesize(text, voice, style, key, destination):
            sf.write(destination, np.ones(2400, dtype=np.float32) * 0.1, 24000)

        with patch("f5_tts.studio.gemini_tts.synthesize", side_effect=synthesize) as generate:
            first = project.prepare([line], gemini_key="test-key")
            self.assertEqual(project.prepare([line], gemini_key="test-key"), first)
            second = project.prepare([line.model_copy(update={"gemini_style": "Sussurro"})], gemini_key="test-key")
            self.assertNotEqual(first[0][0], second[0][0])
            self.assertEqual(
                [call.args[:3] for call in generate.call_args_list],
                [
                    ("Olá!", "Charon", "Calmo"),
                    ("Olá!", "Charon", "Sussurro"),
                ],
            )

    def test_google_cloud_tts_synthesizes_with_project_credentials_and_direction(self):
        from f5_tts.studio import google_cloud_tts
        from f5_tts.studio.dubbing_review import DubbingProject, ReviewLine

        source = self.service.uploads / "source.mp4"
        source.touch()
        row = {"start": 0, "end": 2, "speaker": 1, "text": "Olá!"}
        project = DubbingProject(self.service, source, [row], 2)
        line = ReviewLine(
            **row,
            id=next(iter(project.rows)),
            language="pt",
            model=google_cloud_tts.MODEL_ID,
            gemini_voice="Charon",
            gemini_style="Calmo",
        )
        account = Mock()
        encoded = base64.b64encode(Path(self.reference).read_bytes()).decode("ascii")
        response = httpx.Response(
            200,
            request=httpx.Request("POST", google_cloud_tts.ENDPOINT),
            json={"audioContent": encoded},
        )
        with (
            patch.object(google_cloud_tts, "credentials", return_value=(account, "test-project")),
            patch.object(google_cloud_tts.httpx, "post", return_value=response) as post,
        ):
            clips = project.prepare([line])
        path, duration = clips[0]
        self.assertTrue(path.is_file())
        self.assertGreater(duration, 0)
        account.before_request.assert_called_once()
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-user-project"], "test-project")
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "input": {"text": "Olá!", "prompt": "Calmo"},
                "voice": {"name": "Charon", "languageCode": "pt-BR", "modelName": google_cloud_tts.CLOUD_MODEL},
                "audioConfig": {"audioEncoding": "LINEAR16"},
            },
        )

    def test_cloud_dubbing_resumes_after_failure_and_regenerates_a_single_line(self):
        from f5_tts.studio import google_cloud_tts
        from f5_tts.studio.dubbing_review import DubbingProject, ReviewLine

        source = self.service.uploads / "source.mp4"
        source.touch()
        rows = [
            {"start": 0, "end": 2, "speaker": 1, "text": "Primeira"},
            {"start": 2, "end": 4, "speaker": 1, "text": "Segunda"},
        ]
        project = DubbingProject(self.service, source, rows, 4)
        lines = [
            ReviewLine(id=identifier, model=google_cloud_tts.MODEL_ID, language="pt", **row)
            for identifier, row in zip(project.rows, rows, strict=True)
        ]

        def synthesize(text, _language, _voice, _style, _auth, output):
            if text == "Segunda" and generate.call_count == 2:
                raise ValueError("Cota excedida")
            sf.write(output, np.full(2400, generate.call_count / 10, dtype=np.float32), 24000)

        with (
            patch.object(google_cloud_tts, "credentials", return_value=Mock()),
            patch.object(google_cloud_tts, "synthesize", side_effect=synthesize) as generate,
        ):
            with self.assertRaisesRegex(ValueError, "Cota"):
                project.prepare(lines)
            restored = DubbingProject.restore_draft(self.service, source, rows, project.draft_state())
            first, _ = restored.cloud_voice(lines[0].id, lines[0])
            self.assertTrue(first[0].is_file())
            self.assertEqual(restored.cloud_voice(lines[1].id, lines[1]), (None, False))
            restored.prepare(lines)
            self.assertEqual([call.args[0] for call in generate.call_args_list], ["Primeira", "Segunda", "Segunda"])
            original_bytes = first[0].read_bytes()
            with patch.object(google_cloud_tts, "synthesize", side_effect=ValueError("Cota")):
                with self.assertRaisesRegex(ValueError, "Cota"):
                    restored.regenerate(lines[0].id, lines[0])
            self.assertEqual(first[0].read_bytes(), original_bytes)
            restored.regenerate(lines[0].id, lines[0])
            self.assertNotEqual(first[0].read_bytes(), original_bytes)
            self.assertEqual(generate.call_args.args[0], "Primeira")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_real_ffmpeg_processing_and_mp3_conversion(self):
        result = self.service.process_audio(self.reference, "normalize", "wav")
        self.assertTrue(sf.info(result).duration > 0)
        mp3 = self.service.process_audio(self.reference, "none", "mp3")
        self.assertGreater(Path(mp3).stat().st_size, 100)
        self.assertEqual(len(self.service.read()["processed_audio"]), 2)
        with self.assertRaises(ValueError):
            self.service.process_audio(self.reference, "arbitrary_filter")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class VideoDubbingTests(unittest.TestCase):
    def test_acoustic_profiles_separate_conflicting_voices(self):
        from f5_tts.studio.vocal_profiles import estimate_profile, keep_distinct_profiles

        with tempfile.TemporaryDirectory() as folder:
            profiles = []
            time = np.arange(16000) / 16000
            for frequency in (110, 220, 170):
                wav = Path(folder) / f"{frequency}.wav"
                sf.write(wav, 0.3 * np.sin(2 * np.pi * frequency * time), 16000)
                profiles.append(estimate_profile(wav))
        self.assertEqual(profiles, ["masculine", "feminine", "unknown"])
        self.assertEqual(keep_distinct_profiles([1, 1, 1, 1], [*profiles, "masculine"], [1, 1, 1, 1]), [1, 2, 1, 1])

    def test_known_speaker_count_separates_similar_voices(self):
        rows = [{"start": index * 3, "end": index * 3 + 2} for index in range(4)]
        embeddings = [[1, 0], [0.93, 0.36], [1, 0.02], [0.92, 0.39]]
        self.assertEqual(_assign_speakers(embeddings, rows), [1, 1, 1, 1])
        self.assertEqual(_assign_speakers(embeddings, rows, 2), [1, 2, 1, 2])

    def test_separate_references_and_export_reviewed_dialogue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            studio = StudioService(root / "studio", root / "data")
            studio._translation_factory = lambda text: {"Olá": "Hello", "Bom dia": "Good morning"}[text]
            source = studio.uploads / "input.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:size=160x120:rate=25:d=4",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=4",
                    "-shortest",
                    str(source),
                ],
                check=True,
                capture_output=True,
            )
            original = [
                {"text": "Olá", "start": 0.5, "end": 1.5},
                {"text": "Bom dia", "start": 2.0, "end": 3.2},
            ]
            recorded_cast = []

            def synthesize(python, cast, language, device, **kwargs):
                self.assertEqual(language, "en")
                for reference, lines in cast:
                    self.assertTrue(reference.is_file())
                    for text, clip in lines:
                        recorded_cast.append((reference, text))
                        sf.write(clip, np.sin(np.arange(48000) * 0.1) * 0.2, 24000, subtype="FLOAT")

            with (
                patch.object(studio, "_transcribe_audio", return_value=original) as asr,
                patch.object(chatterbox, "chatterbox_python", return_value=root / "python"),
                patch.object(chatterbox, "identify_voices", return_value=[[1.0, 0.0], [0.0, 1.0]]),
                patch.object(chatterbox, "synthesize_cast", side_effect=synthesize),
            ):
                (root / "python").touch()
                segments = analyze_video(studio, source, "pt", "openai/whisper-large-v3-turbo")
                self.assertEqual(asr.call_args.args[1:], ("pt", "openai/whisper-large-v3-turbo"))
                cast = speaker_references(segments)
                self.assertEqual(list(cast), [1, 2])
                self.assertNotEqual(cast[1]["reference"], cast[2]["reference"])
                dialogue = translate_dialogue(studio, segments, "pt", "en")
                self.assertEqual(dialogue, ["Hello", "Good morning"])
                dialogue[0] = "Hi"
                video, captions, aligned = dub_video(studio, source, segments, dialogue, "en", chatterbox.MODEL_ID)
            self.assertEqual(recorded_cast, [(cast[1]["reference"], "Hi"), (cast[2]["reference"], "Good morning")])
            streams = json.loads(
                subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(video)])
            )["streams"]
            self.assertEqual([stream["codec_name"] for stream in streams], ["h264", "aac"])
            self.assertAlmostEqual(float(streams[0]["duration"]), 4, places=2)
            self.assertIn("00:00:00,500 --> 00:00:01,780\nHi", captions.read_text(encoding="utf-8"))
            self.assertGreater(aligned[0]["speed"], 1.3)
            self.assertLess(aligned[0]["end"], aligned[1]["start"])
            audio = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-f", "f32le", "-"])
            samples = np.frombuffer(audio, dtype="<f4")
            self.assertLess(np.max(np.abs(samples[: int(0.4 * 48000)])), 0.002)
            self.assertGreater(np.max(np.abs(samples[int(0.6 * 48000) : int(0.9 * 48000)])), 0.02)

    def test_gemini_transcription_extracts_original_references_from_reported_speakers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            studio = StudioService(root / "studio", root / "data")
            source = studio.uploads / "input.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:s=160x120:d=3",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=3",
                    "-shortest",
                    str(source),
                ],
                check=True,
                capture_output=True,
            )
            payload = {
                "segments": [
                    {"speaker": 8, "startSeconds": 0, "endSeconds": 1.25, "text": "Olá"},
                    {"speaker": 3, "startSeconds": 1.25, "endSeconds": 2, "text": "Oi"},
                    {"speaker": 8, "startSeconds": 2, "endSeconds": 3, "text": "Tudo bem?"},
                ]
            }
            with (
                patch("f5_tts.studio.script_review.httpx.post") as request,
                patch("f5_tts.studio.script_review.time.sleep") as wait,
            ):
                request.return_value.json.return_value = {
                    "candidates": [
                        {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(payload)}]}},
                    ]
                }
                request.side_effect = [httpx.Response(503), request.return_value]
                rows = analyze_video(studio, source, "pt", GEMINI_TRANSCRIPTION, gemini_key="test-key", speaker_count=2)
                wait.assert_called_once_with(2)
                self.assertEqual(request.call_count, 2)
            self.assertEqual([row["speaker"] for row in rows], [1, 2, 1])
            self.assertEqual(rows[1]["start"], 1.25)
            self.assertAlmostEqual(rows[-1]["end"], 3, delta=0.03)
            self.assertTrue(all(row["reference"].is_file() for row in rows))
            self.assertIn(
                "expects 2 distinct speaking voices",
                request.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"],
            )
            self.assertEqual(
                request.call_args.kwargs["json"]["contents"][0]["parts"][1]["inlineData"]["mimeType"], "audio/mp3"
            )
            studio.set_offline(True)
            with self.assertRaisesRegex(ValueError, "modo offline"):
                analyze_video(studio, source, "pt", GEMINI_TRANSCRIPTION, gemini_key="test-key")

    def test_review_routes_regenerate_one_line_and_export_stream_copy(self):
        import asyncio

        import httpx
        from fastapi import FastAPI

        from f5_tts.studio.dubbing_review import DubbingProject, ExportRequest
        from f5_tts.studio.dubbing_review_api import register_dubbing_routes

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            studio = StudioService(root / "studio", root / "data")
            source = studio.uploads / "review.mp4"
            reference = studio.uploads / "reference.wav"
            sf.write(reference, np.ones(24000) * 0.1, 24000)
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:s=160x120:rate=25:d=3",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=3",
                    "-shortest",
                    str(source),
                ],
                check=True,
                capture_output=True,
            )
            project = DubbingProject(
                studio,
                source,
                [{"text": "Olá", "start": 0.61, "end": 1.31, "speaker": 1, "reference": reference}],
                3,
            )
            app = FastAPI()
            register_dubbing_routes(app, {project.id: project})
            sounds = []

            def synthesize(_python, cast, _language, _device, **_options):
                for _reference, lines in cast:
                    for text, path in lines:
                        sounds.append(text)
                        sf.write(path, np.sin(np.arange(19200) * 0.1) * 0.2, 24000)

            identifier = next(iter(project.rows))
            request = {"text": "Hello", "language": "en", "speaker": 1}

            async def exercise_routes():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                    response = await client.post(
                        f"/api/dubbing/{project.id}/segments/{identifier}/regenerate", json=request
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["duration"], 0.8)
                    self.assertEqual((await client.get(response.json()["audio_url"])).status_code, 200)
                    line = {**request, "id": str(identifier), "start": 0.61, "end": 1.31}
                    refused = await client.post(
                        f"/api/dubbing/{project.id}/export", json={"lines": [line], "auto_speed": False}
                    )
                    self.assertEqual(refused.status_code, 422, refused.text)
                    response = await client.post(f"/api/dubbing/{project.id}/export", json={"lines": [line]})
                    self.assertEqual(response.status_code, 200, response.text)
                    return project.directory / response.json()["video_url"].split("/")[-1]

            with (
                patch.object(chatterbox, "chatterbox_python", return_value=reference),
                patch.object(chatterbox, "synthesize_cast", side_effect=synthesize),
            ):
                video = asyncio.run(asyncio.wait_for(exercise_routes(), 15))
                events = []
                preview_request = ExportRequest(
                    lines=[{**request, "id": str(identifier), "start": 0.61, "end": 1.1}],
                    auto_speed=False,
                )
                preview, _, captions = project.export(
                    preview_request,
                    preview=True,
                    on_progress=lambda value, message, **kwargs: events.append(value),
                )
                self.assertTrue(preview.name.endswith("-previa-dublado.mp4"))
                self.assertTrue(captions[0]["preview_overflow"])
                self.assertEqual(events, sorted(events))
                self.assertEqual(events[-1], 1)
            self.assertEqual(sounds, ["Hello"])
            hashes = [
                subprocess.check_output(
                    ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-c:v", "copy", "-f", "hash", "-"]
                )
                for path in (source, video)
            ]
            self.assertEqual(*hashes)
            samples = np.frombuffer(
                subprocess.check_output(
                    ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-f", "f32le", "-"]
                ),
                dtype="<f4",
            )
            self.assertLess(np.max(np.abs(samples[: int(0.58 * 48000)])), 0.005)
            self.assertGreater(np.max(np.abs(samples[int(0.64 * 48000) : int(0.8 * 48000)])), 0.01)

    def test_hybrid_export_uses_selected_human_take_and_ai_with_independent_delays(self):
        import asyncio

        import httpx
        from fastapi import FastAPI

        from f5_tts.studio.dubbing_review import DubbingProject
        from f5_tts.studio.dubbing_review_api import register_dubbing_routes

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            studio = StudioService(root / "studio", root / "data")
            source = studio.uploads / "hybrid.mp4"
            audio = studio.uploads / "take.wav"
            sf.write(audio, np.sin(np.arange(16000) * 0.11) * 0.15, 32000)
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=160x120:r=25:d=3", str(source)],
                check=True,
            )
            project = DubbingProject(
                studio,
                source,
                [
                    {"text": "IA", "start": 0.3, "end": 1, "speaker": 1, "reference": audio},
                    {"text": "ADR", "start": 1.5, "end": 2.4, "speaker": 2},
                ],
                3,
            )
            ai, human = project.rows
            app = FastAPI()
            register_dubbing_routes(app, {project.id: project})
            voice = {"language": "en", "speaker": 1, "text": "AI"}
            take_route = f"/api/dubbing/{project.id}/segments/{human}/takes"
            synthesis = []

            def synthesize(_python, cast, *_args, **_kwargs):
                for _, lines in cast:
                    for text, output in lines:
                        synthesis.append(text)
                        shutil.copyfile(audio, output)

            async def use_routes():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:

                    async def upload():
                        response = await client.post(
                            take_route + "?capture_offset=30",
                            content=audio.read_bytes(),
                            headers={"content-type": "audio/wav"},
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        return response.json()

                    first = await upload()
                    second = await upload()
                    chosen = await client.patch(take_route, json={"take_id": first["id"]})
                    self.assertEqual(chosen.status_code, 200)
                    self.assertEqual((await client.delete(f"{take_route}/{second['id']}")).status_code, 200)
                    take = next(iter(project.takes[human].values()))
                    self.assertEqual(take.path.parent.name, human.hex)
                    self.assertEqual(sf.info(take.path).samplerate, 48000)
                    self.assertEqual(sf.info(take.path).channels, 1)
                    response = await client.post(
                        f"/api/dubbing/{project.id}/export",
                        json={
                            "lines": [
                                {**voice, "id": str(ai), "start": 0.3, "end": 1},
                                {
                                    "id": str(human),
                                    "text": "Human",
                                    "speaker": 2,
                                    "language": "en",
                                    "start": 1.5,
                                    "end": 2.4,
                                    "offset_ms": -10,
                                },
                            ],
                            "latency_ms": 60,
                            "treat_human": True,
                            "background_volume": 70,
                        },
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    return project.directory / response.json()["video_url"].rsplit("/", 1)[1]

            with (
                patch.object(chatterbox, "chatterbox_python", return_value=audio),
                patch.object(chatterbox, "synthesize_cast", side_effect=synthesize),
            ):
                video = asyncio.run(asyncio.wait_for(use_routes(), 20))
            self.assertEqual(synthesis, ["AI"])
            decoded = subprocess.check_output(
                ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-f", "f32le", "-"]
            )
            samples = np.frombuffer(decoded, dtype="<f4")
            self.assertLess(np.max(np.abs(samples[int(1.12 * 48000) : int(1.54 * 48000)])), 0.005)
            self.assertGreater(np.max(np.abs(samples[int(1.62 * 48000) : int(1.85 * 48000)])), 0.01)

    def test_advanced_editor_splits_linked_video_and_audio_and_renders_pitch_and_text(self):
        from f5_tts.studio.montage import MontageStore, duplicate_clip, render_montage, split_at

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            studio = StudioService(root / "studio", root / "data")
            source = studio.uploads / "video.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:s=160x120:r=25:d=2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=440:d=2",
                    "-shortest",
                    str(source),
                ],
                check=True,
                capture_output=True,
            )
            store = MontageStore(studio)
            project = store.create("Teste", [(source, "Cena")])
            video, sound = project["clips"]
            self.assertEqual((video["track"], sound["track"]), ("V1", "A1"))
            self.assertFalse(video["has_audio"])
            self.assertEqual(video["link"], sound["link"])
            halves, right = split_at(project["clips"], video, 1)
            self.assertEqual([row["start"] for row in halves], [0, 1, 0, 1])
            self.assertEqual(halves[1]["link"], halves[3]["link"])
            copies, _ = duplicate_clip(halves, next(row for row in halves if row["id"] == right))
            self.assertEqual([row["start"] for row in copies[-2:]], [2, 2])
            self.assertEqual(copies[-1]["link"], copies[-2]["link"])
            self.assertNotEqual(copies[-1]["link"], halves[1]["link"])
            project["clips"] = [
                video,
                {**sound, "pitch": 12},
                {
                    "id": "title",
                    "kind": "text",
                    "track": "TX",
                    "name": "Abertura",
                    "text": "Abertura",
                    "start": 0.1,
                    "in": 0,
                    "out": 0.9,
                    "duration": 0.9,
                    "font_size": 52,
                    "volume": 100,
                    "has_audio": False,
                },
            ]
            output, _ = render_montage(studio, project, preview=True)
            audio = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    "0.4",
                    "-i",
                    str(output),
                    "-t",
                    "0.5",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "48000",
                    "-f",
                    "f32le",
                    "-",
                ]
            )
            samples = np.frombuffer(audio, dtype="<f4")
            frequencies = np.fft.rfftfreq(len(samples), 1 / 48000)
            strongest = frequencies[np.argmax(np.abs(np.fft.rfft(samples * np.hanning(len(samples)))))]
            self.assertAlmostEqual(strongest, 880, delta=12)
            frame = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    "0.5",
                    "-i",
                    str(output),
                    "-vf",
                    "scale=320:180",
                    "-frames:v",
                    "1",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "-",
                ]
            )
            pixels = np.frombuffer(frame, dtype=np.uint8).reshape(180, 320, 3)
            self.assertTrue((pixels[65:115, 95:225, :] > 210).all(axis=2).any())
            self.assertAlmostEqual(video_duration(output), 2, delta=0.05)

    def test_montage_transitions_blend_video_and_audio_in_an_overlap(self):
        from f5_tts.studio.montage import MontageStore, render_montage
        from f5_tts.studio.montage_transitions import overlap_start

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            studio = StudioService(root / "studio", root / "data")
            store = MontageStore(studio)
            red, blue = (studio.uploads / filename for filename in ("red.mp4", "blue.mp4"))
            for source, color in ((red, "red"), (blue, "blue")):
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color={color}:s=160x90:r=30:d=1", str(source)],
                    check=True,
                    capture_output=True,
                )
            first, second = store.clip(red, "Primeiro", 0), store.clip(blue, "Segundo", 1)
            _, start, length = overlap_start([first, second], second, 0.4)
            self.assertEqual(start, 0.6)
            second.update(start=start, video_transition={"kind": "dissolve", "duration": length})
            tones = []
            for index, frequency in enumerate((400, 800)):
                source = studio.uploads / f"tone-{index}.wav"
                sf.write(source, np.sin(2 * np.pi * frequency * np.arange(48000) / 48000) * 0.12, 48000)
                clip = store.clip(source, f"Áudio {index}", start if index else 0)
                clip["audio_transition"] = {("fade_in" if index else "fade_out"): 0.4, "curve": "qsin"}
                tones.append(clip)
            project = {**store.create("Transições"), "clips": [first, second, *tones]}
            video, _ = render_montage(studio, project, preview=True)
            frame = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    "0.8",
                    "-i",
                    str(video),
                    "-vf",
                    "scale=16:10",
                    "-frames:v",
                    "1",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "-",
                ]
            )
            red_value, _green, blue_value = np.frombuffer(frame, np.uint8).reshape(10, 16, 3)[5, 8]
            self.assertTrue(75 < red_value < 180 and 75 < blue_value < 180, (red_value, blue_value))
            raw = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    "0.76",
                    "-i",
                    str(video),
                    "-t",
                    "0.08",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "48000",
                    "-f",
                    "f32le",
                    "-",
                ]
            )
            samples = np.frombuffer(raw, dtype="<f4")
            frequencies = np.fft.rfftfreq(len(samples), 1 / 48000)
            levels = np.abs(np.fft.rfft(samples))
            low, high = (levels[np.argmin(np.abs(frequencies - hz))] for hz in (400, 800))
            self.assertGreater(min(low, high) / max(low, high), 0.65)

    def test_editor_trims_and_joins_clips_with_music_and_styled_captions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            studio = StudioService(root / "studio", root / "data")
            dubbed = studio.outputs / "teste-dublado.mp4"
            clip = studio.uploads / "extra.mp4"
            music = studio.uploads / "music.wav"
            inputs = [
                (
                    dubbed,
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        "color=blue:size=320x180:rate=30:d=4",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=440:duration=4",
                        "-shortest",
                    ],
                ),
                (clip, ["-f", "lavfi", "-i", "color=green:size=200x200:rate=25:d=2"]),
            ]
            for target, args in inputs:
                subprocess.run(["ffmpeg", "-v", "error", *args, str(target)], check=True, capture_output=True)
            sf.write(music, np.sin(np.arange(12000) * 0.1) * 0.1, 24000)
            cues = [
                {"start": 0.5, "end": 1.5, "text": "Legenda na dublagem"},
                {"start": 4.5, "end": 5.5, "text": "Legenda no segundo clipe"},
            ]
            edited, captions = edit_dubbed_video(
                studio,
                dubbed,
                cues,
                1,
                3,
                80,
                style="yellow",
                burn=True,
                clips=[clip],
                transition="fade",
                music=music,
                music_volume=20,
            )
            self.assertTrue(dubbed.is_file())
            self.assertAlmostEqual(video_duration(edited), 4, delta=0.1)
            text = captions.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:00,500\nLegenda na dublagem", text)
            self.assertIn("00:00:02,500 --> 00:00:03,500\nLegenda no segundo clipe", text)

            frames = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    "2.8",
                    "-i",
                    str(edited),
                    "-frames:v",
                    "1",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "-",
                ]
            )
            pixels = np.frombuffer(frames, dtype=np.uint8).reshape(180, 320, 3)
            self.assertTrue(((pixels[:, :, 0] > 180) & (pixels[:, :, 1] > 180) & (pixels[:, :, 2] < 90)).any())
            audio = subprocess.check_output(
                ["ffmpeg", "-v", "error", "-ss", "2.8", "-i", str(edited), "-t", "0.2", "-vn", "-f", "f32le", "-"]
            )
            self.assertGreater(np.max(np.abs(np.frombuffer(audio, dtype="<f4"))), 0.005)


if __name__ == "__main__":
    unittest.main()
