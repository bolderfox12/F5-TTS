"""Gera amostras reais no histórico do Studio usando a referência do repositório."""
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(ROOT / ".cache/huggingface"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache/matplotlib"))
os.environ.setdefault("NUMBA_CACHE_DIR", str(ROOT / ".cache/numba"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import numpy as np
import soundfile as sf
import torch

from f5_tts.studio.service import StudioService

if __name__ == "__main__":
    assert torch.cuda.is_available(), "CUDA precisa estar disponível para este teste."
    service = StudioService(home=ROOT / ".studio")
    service.settings("cuda", 32, 1.0)
    reference = ROOT / "src/f5_tts/infer/examples/basic/basic_ref_en.wav"
    voice = next((v for v in service.read()["voices"] if v["name"] == "Referência de demonstração (inglês)"), None)
    if voice is None:
        uploaded = service.upload_path(reference.name)
        shutil.copy2(reference, uploaded)
        voice = service.save_voice(
            "Referência de demonstração (inglês)", str(uploaded),
            "Some call me nature, others call me mother nature.",
        )
    results = []
    for language, text in [
        ("en", "Welcome to F5 Studio. This voice was generated locally on this computer."),
        ("pt", "Olá! Este é um teste de geração de voz no F5 Studio, executado neste computador."),
    ]:
        entry = service.generate(text, voice_id=voice["id"])
        audio, rate = sf.read(entry["path"])
        assert rate == 24000 and len(audio) > rate
        assert np.isfinite(audio).all() and np.max(np.abs(audio)) > 0.001
        assert np.max(np.abs(audio)) < 1.0, "O WAV não deve saturar."
        results.append({"language": language, "sample_rate": rate, "seconds": len(audio) / rate,
                        "peak": float(np.max(np.abs(audio))), "entry": entry})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    (ROOT / "validacao-modelo-real.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
