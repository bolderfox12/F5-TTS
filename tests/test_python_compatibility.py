"""Offline checks: audio I/O, tokenizer, model forward/backward, and imports."""

import importlib
import tempfile
import unittest
from pathlib import Path

import torch

from f5_tts.audio import audio_info, load_audio, save_audio
from f5_tts.model import DiT
from f5_tts.model.utils import convert_char_to_pinyin


class CompatibilityTests(unittest.TestCase):
    def test_audio_roundtrip_and_metadata(self):
        for channels in (1, 2):
            with self.subTest(channels=channels), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "sample.wav"
                waveform = torch.linspace(-0.5, 0.5, 2400).repeat(channels, 1)
                save_audio(path, waveform, 24000)
                actual, rate = load_audio(path)
                self.assertEqual(rate, 24000)
                self.assertEqual(actual.shape, waveform.shape)
                torch.testing.assert_close(actual, waveform, atol=4e-5, rtol=0)
                info = audio_info(path)
                self.assertEqual((info.sample_rate, info.num_frames), (24000, 2400))

    def test_invalid_audio_shape(self):
        with self.assertRaises(ValueError):
            save_audio("unused.wav", torch.zeros(8), 24000)

    def test_wav_export_prevents_clipping_without_boosting_quiet_audio(self):
        import numpy as np
        import soundfile as sf

        from f5_tts.api import F5TTS

        api = F5TTS.__new__(F5TTS)  # No checkpoint download needed for export.
        api.target_sample_rate = 24000
        for source in (np.array([-2.0, -1.0, 0.0, 1.0, 2.0]), np.array([-0.5, 0.0, 0.5])):
            with self.subTest(peak=np.max(np.abs(source))), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "export.wav"
                api.export_wav(source, path)
                actual, rate = sf.read(path)
                expected = source * (0.99 / 2) if np.max(np.abs(source)) > 1 else source
                self.assertEqual(rate, 24000)
                np.testing.assert_allclose(actual, expected, atol=4e-5)

    def test_model_forward_backward(self):
        model = DiT(dim=64, depth=1, heads=2, dim_head=32, mel_dim=8, text_dim=16)
        audio = torch.randn(1, 16, 8)
        output = model(audio, torch.zeros_like(audio), torch.tensor([[1, 2, 3]]), torch.tensor([0.5]))
        self.assertEqual(output.shape, audio.shape)
        self.assertTrue(torch.isfinite(output).all())
        output.square().mean().backward()
        self.assertTrue(any(p.grad is not None for p in model.parameters()))

    def test_tokenizer(self):
        self.assertTrue(convert_char_to_pinyin(["Olá, mundo! 你好"]))

    def test_imports_without_checkpoints(self):
        for name in ("f5_tts.model.trainer", "f5_tts.model.dataset", "f5_tts.infer.utils_infer", "hydra", "gradio"):
            with self.subTest(module=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
