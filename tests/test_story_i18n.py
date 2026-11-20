"""Story casting, reusable audio, and offline interface languages."""

import ast
import copy
import json
import tempfile
import unittest
from pathlib import Path
from string import Formatter
from unittest.mock import patch

import numpy as np
import soundfile as sf

from f5_tts.studio import chatterbox_backend as chatterbox
from f5_tts.studio.i18n import INTERFACE_LANGUAGES, catalogs, translate
from f5_tts.studio.service import StudioService
from f5_tts.studio.story_tools import parse_character_script


class RecordingModel:
    calls = []

    def __init__(self, **options):
        self.options = options

    def infer(self, **options):
        self.calls.append({"model": self.options, **options})
        sf.write(options["file_wave"], np.ones(24000, dtype=np.float32) * 0.1, 24000)


class StoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.service = StudioService(self.root / "studio", self.root / "data", model_factory=RecordingModel)
        RecordingModel.calls = []
        self.cast = []
        for index, name in enumerate(("Narrador", "Ana", "Bruno")):
            path = self.service.upload_path(name + ".wav")
            sf.write(path, np.ones(48000, dtype=np.float32) * 0.1, 24000)
            voice = self.service.save_voice(name, path, "Reference " + name)
            self.cast.append({"id": str(index), "name": name, "voice": voice["id"], "model_id": "original"})
        self.script = "Narrador: Uma história.\nAna: Olá!\nBruno: Tudo bem?"

    def tearDown(self):
        self.folder.cleanup()

    def test_cast_generates_different_references_and_speaker_subtitles(self):
        project = self.service.save_story(None, "Minha história", self.script, self.cast)
        self.assertEqual(len(project["blocks"]), 3)
        mixed = self.service.render_narration(project["id"])
        self.assertEqual(
            [call["ref_text"] for call in RecordingModel.calls],
            ["Reference Narrador", "Reference Ana", "Reference Bruno"],
        )
        self.assertAlmostEqual(sf.info(mixed["path"]).duration, 3.8, places=2)
        subtitles = Path(mixed["srt"]).read_text()
        self.assertIn("Ana: Olá!", subtitles)
        self.assertEqual(mixed["segments"][1]["start"], 1.4)
        self.assertEqual(len(StudioService(self.service.home, self.service.data_root).stories()), 1)

    def test_editing_one_line_reuses_other_audio_and_reordering_keeps_voices(self):
        project = self.service.save_story(None, "História", self.script, self.cast)
        self.service.render_narration(project["id"])
        before = self.service.narration(project["id"])
        edited = self.service.save_story(project["id"], "História", self.script.replace("Olá!", "Cheguei!"), self.cast)
        self.assertEqual(edited["blocks"][0]["id"], before["blocks"][0]["id"])
        self.assertTrue(self.service.block_ready(edited["blocks"][0]))
        self.assertFalse(self.service.block_ready(edited["blocks"][1]))
        self.service.render_narration(project["id"])
        self.assertEqual(len(RecordingModel.calls), 4)
        self.service.move_block(project["id"], edited["blocks"][2]["id"], -1)
        self.service.render_narration(project["id"])
        self.assertEqual(len(RecordingModel.calls), 4)

    def test_character_model_changes_and_checkpoint_updates_invalidate_only_affected_audio(self):
        checkpoint = self.root / "portuguese.pt"
        checkpoint.write_bytes(b"model version one")
        model = self.service.register_model("Português", checkpoint)
        self.cast[1]["model_id"] = model["id"]
        project = self.service.save_story(None, "Models", self.script, self.cast)
        self.service.render_narration(project["id"])
        self.assertEqual(RecordingModel.calls[1]["model"]["ckpt_file"], str(checkpoint))
        blocks = self.service.narration(project["id"])["blocks"]
        self.assertTrue(all(self.service.block_ready(block) for block in blocks))
        checkpoint.write_bytes(b"model version two, different size")
        self.assertTrue(self.service.block_ready(blocks[0]))
        self.assertFalse(self.service.block_ready(blocks[1]))
        self.service.render_narration(project["id"])
        self.assertEqual(len(RecordingModel.calls), 4)

    def test_chatterbox_language_change_invalidates_only_that_characters_audio(self):
        self.cast[1].update(model_id=chatterbox.MODEL_ID, language="es")
        python = chatterbox.chatterbox_python(self.service.home)
        python.parent.mkdir(parents=True)
        python.touch()
        project = self.service.save_story(None, "Models", self.script, self.cast)

        def synthesize(_python, _text, _reference, language, _device, output, *, cancel_event=None):
            self.assertEqual(language, "es")
            sf.write(output, np.ones(24000, dtype=np.float32) * 0.1, 24000)

        with patch.object(chatterbox, "synthesize", side_effect=synthesize):
            self.service.render_narration(project["id"])
        blocks = self.service.narration(project["id"])["blocks"]
        self.assertTrue(all(self.service.block_ready(block) for block in blocks))

        self.cast[1]["language"] = "pt"
        edited = self.service.save_story(project["id"], "Models", self.script, self.cast)
        self.assertEqual([self.service.block_ready(block) for block in edited["blocks"]], [True, False, True])
        self.assertEqual(edited["blocks"][1]["language"], "pt")

    def test_renaming_character_keeps_audio_and_updates_subtitles(self):
        project = self.service.save_story(None, "Rename", self.script, self.cast)
        self.service.render_narration(project["id"])
        cast = copy.deepcopy(self.cast)
        cast[1]["name"] = "Alice"
        self.service.save_story(project["id"], "Rename", self.script.replace("Ana:", "Alice:"), cast)
        result = self.service.render_narration(project["id"])
        self.assertEqual(len(RecordingModel.calls), 3)
        self.assertIn("Alice: Olá!", Path(result["srt"]).read_text())

    def test_invalid_cast_or_unknown_speaker_does_not_modify_saved_story(self):
        project = self.service.save_story(None, "Safe", self.script, self.cast)
        before = json.loads(json.dumps(self.service.read()))
        for script, cast in [
            ("Someone: Hello", self.cast),
            ("", self.cast),
            (self.script, []),
            (self.script, [self.cast[0], self.cast[0]]),
        ]:
            with self.subTest(script=script), self.assertRaises(ValueError):
                self.service.save_story(project["id"], "Changed", script, cast)
            self.assertEqual(self.service.read(), before)

    def test_multiline_speech_colons_and_long_turns(self):
        turns = parse_character_script(
            "Opening without label.\n\nAna: At 12:30.\nNext line.\nBRUNO: " + "word " * 600, self.cast
        )
        self.assertEqual(turns[0]["character"], self.cast[0]["id"])
        self.assertEqual(turns[1]["text"], "At 12:30. Next line.")
        self.assertGreater(len(turns), 3)
        self.assertTrue(all(len(turn["text"]) <= 1200 for turn in turns))

    def test_edit_delete_and_language_preserve_user_content(self):
        project = self.service.save_story(None, "Português: Meu conto", self.script, self.cast)
        self.service.edit_story_block(project["id"], project["blocks"][1]["id"], "Nova fala", self.cast[2]["id"], 0.7)
        self.service.delete_story_block(project["id"], project["blocks"][0]["id"])
        saved = self.service.narration(project["id"])
        self.service.set_interface_language("ja")
        self.assertEqual(self.service.narration(project["id"]), saved)
        reopened = StudioService(self.service.home, self.service.data_root)
        self.assertEqual(reopened.interface_language(), "ja")
        self.assertEqual(reopened.stories()[0]["name"], "Português: Meu conto")
        with self.assertRaises(ValueError):
            self.service.set_interface_language("unsupported")

    def test_chinese_and_japanese_speaker_labels_accept_fullwidth_colons(self):
        cast = copy.deepcopy(self.cast)
        cast[0]["name"] = "旁白"
        cast[1]["name"] = "ナレーター"
        turns = parse_character_script("旁白：故事开始。\nナレーター：こんにちは。", cast)
        self.assertEqual([turn["text"] for turn in turns], ["故事开始。", "こんにちは。"])


class TranslationTests(unittest.TestCase):
    def test_all_locales_have_every_message_and_matching_placeholders(self):
        def fields(text):
            return {name for _, name, _, _ in Formatter().parse(text) if name}

        messages = catalogs()
        self.assertEqual(len(INTERFACE_LANGUAGES), 7)
        source = messages["pt-BR"]
        for locale, translated in messages.items():
            self.assertEqual(set(source), set(translated), locale)
            for key, value in translated.items():
                with self.subTest(locale=locale, key=key):
                    self.assertTrue(value.strip())
                    self.assertEqual(fields(key), fields(value))

    def test_wrapped_ui_literals_have_catalog_entries(self):
        root = Path(__file__).resolve().parents[1] / "src/f5_tts/studio"
        for path in root.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "t"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    self.assertIn(node.args[0].value, catalogs()["en-US"], f"{path.name}:{node.lineno}")

    def test_dynamic_errors_templates_and_user_text(self):
        self.assertEqual(
            translate("Personagem não cadastrado na linha 2: Ana", "en-US"), "Unknown character on line 2: Ana"
        )
        self.assertEqual(translate("Trecho 2 de 10", "de"), "Abschnitt 2 von 10")
        self.assertEqual(translate("Histórias e personagens", "ja"), "物語と登場人物")
        self.assertEqual(translate("User-owned story text: Ana", "fr"), "User-owned story text: Ana")
        for locale in INTERFACE_LANGUAGES:
            template = next(key for key in catalogs()[locale] if key.startswith("{narrator}: Era"))
            result = translate(template, locale, narrator="My narrator", first="Ana", second="Bruno")
            self.assertIn("My narrator:", result)


if __name__ == "__main__":
    unittest.main()
