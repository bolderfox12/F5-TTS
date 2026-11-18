"""Prepare Studio datasets while preserving vocabulary IDs used by the base model."""

import hashlib
import json
import tempfile
from pathlib import Path

import soundfile as sf
from datasets.arrow_writer import ArrowWriter

from f5_tts.model.utils import convert_char_to_pinyin


def read_vocabulary(path):
    tokens = Path(path).read_text(encoding="utf-8-sig").split("\n")
    if tokens[-1] == "":
        tokens.pop()
    if not tokens or tokens[0] != " " or "" in tokens or len(tokens) != len(set(tokens)):
        raise ValueError("O vocabulário deve começar com um espaço e ter um token único por linha.")
    return tokens


def vocabulary_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare_dataset(project, base_vocab, allow_extension, has_checkpoints):
    data = project.read()
    approved = [row for row in data["examples"] if row["approved"]]
    project.export_reviewed()
    base_tokens = read_vocabulary(base_vocab)
    vocab_path = project.path / "vocab.txt"
    recorded_base = data.get("prepared_base_vocabulary")
    if has_checkpoints and recorded_base and recorded_base != vocabulary_digest(base_vocab):
        raise ValueError("Use o mesmo vocabulário base para retomar este treino.")
    tokens = read_vocabulary(vocab_path) if has_checkpoints and vocab_path.exists() else base_tokens
    if tokens[: len(base_tokens)] != base_tokens:
        raise ValueError("O vocabulário base tem IDs diferentes dos usados neste projeto.")
    text_tokens = convert_char_to_pinyin([row["text"] for row in approved], polyphone=True)
    missing = sorted(set().union(*map(set, text_tokens)) - set(tokens))
    if missing and (not allow_extension or has_checkpoints):
        preview = ", ".join(repr(token) for token in missing[:20])
        if has_checkpoints:
            raise ValueError(f"Há tokens novos ({preview}). Crie um projeto para iniciar com vocabulário ampliado.")
        raise ValueError(f"Há tokens fora do vocabulário ({preview}). Ative a ampliação e prepare novamente.")

    durations = []
    with tempfile.TemporaryDirectory(dir=project.path) as folder:
        temporary = Path(folder)
        with ArrowWriter(path=str(temporary / "raw.arrow")) as writer:
            for row, text in zip(approved, text_tokens, strict=True):
                audio_path = project.path / "wavs" / (row["id"] + ".wav")
                duration = sf.info(audio_path).duration
                if not 1 <= duration <= 30:
                    raise ValueError(f"Trecho {row['id']} fora do intervalo de 1 a 30 segundos.")
                durations.append(duration)
                writer.write({"audio_path": str(audio_path), "text": text, "duration": duration})
            writer.finalize()
        (temporary / "duration.json").write_text(json.dumps({"duration": durations}), encoding="utf-8")
        (temporary / "vocab.txt").write_text("\n".join(tokens + missing) + "\n", encoding="utf-8")

        data.pop("prepared", None)
        project.write(data)
        for filename in ("raw.arrow", "duration.json", "vocab.txt"):
            (temporary / filename).replace(project.path / filename)
        data["prepared"] = project.fingerprint()
        data["prepared_base_vocabulary"] = vocabulary_digest(base_vocab)
        data["prepared_vocabulary"] = vocabulary_digest(vocab_path)
        data["base_vocabulary_rows"] = len(base_tokens) + 1
        project.write(data)
    return f"{project.summary()}\n{len(tokens) + len(missing)} tokens · {len(missing)} adicionados nesta preparação."
