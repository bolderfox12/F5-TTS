"""Local narration projects, subtitles, translation and audio processing."""

import gc
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
import soundfile as sf
import torch
import torchaudio


LANGUAGES = {"pt": "por_Latn", "en": "eng_Latn", "es": "spa_Latn", "fr": "fra_Latn", "de": "deu_Latn"}
ASR_MODELS = {"openai/whisper-small", "openai/whisper-large-v3-turbo"}


def srt_time(seconds):
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def subtitles(segments):
    return (
        "\n\n".join(
            f"{i}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n{s['text'].strip()}"
            for i, s in enumerate(segments, 1)
        )
        + "\n"
    )


class LocalTools:
    def narrations(self):
        return self.read().get("narrations", [])

    def narration(self, identifier):
        item = next((p for p in self.narrations() if p["id"] == identifier), None)
        if item is None:
            raise ValueError("Abra um projeto de narração existente.")
        return item

    def create_narration(self, name, script, voice):
        if not name or not name.strip() or not script or not script.strip():
            raise ValueError("Dê um nome ao projeto e escreva o roteiro.")
        if voice not in {v["id"] for v in self.read()["voices"]}:
            raise ValueError("Salve uma referência em Minhas vozes e escolha a voz inicial.")
        paragraphs = []
        for paragraph in re.split(r"\n\s*\n", script.strip()):
            words, chunk = paragraph.split(), ""
            for word in words:
                if len(chunk) + len(word) > 1200 and chunk:
                    paragraphs.append(chunk)
                    chunk = ""
                chunk += (" " if chunk else "") + word
            if chunk:
                paragraphs.append(chunk)
        project = {
            "id": uuid4().hex,
            "name": name.strip(),
            "blocks": [
                {"id": uuid4().hex, "text": text, "voice": voice, "pause": 0.4, "render": None} for text in paragraphs
            ],
            "mix": None,
        }
        with self._storage:
            data = self.read()
            data.setdefault("narrations", []).append(project)
            self.write(data)
        return project

    def edit_block(self, project_id, block_id, text, voice, pause):
        if not text or not text.strip() or not 0 <= float(pause) <= 10:
            raise ValueError("Escreva o trecho e escolha uma pausa entre 0 e 10 segundos.")
        with self._storage:
            data = self.read()
            if voice not in {v["id"] for v in data["voices"]}:
                raise ValueError("Escolha uma voz salva.")
            project = next((p for p in data.get("narrations", []) if p["id"] == project_id), None)
            block = next((b for b in project["blocks"] if b["id"] == block_id), None) if project else None
            if block is None:
                raise ValueError("Selecione um trecho do projeto.")
            block.update(text=text.strip(), voice=voice, pause=float(pause))
            project["mix"] = None
            self.write(data)
        return self.narration(project_id)

    def move_block(self, project_id, block_id, direction):
        if direction not in (-1, 1):
            raise ValueError("Direção inválida.")
        with self._storage:
            data = self.read()
            project = next((p for p in data.get("narrations", []) if p["id"] == project_id), None)
            if not project:
                raise ValueError("Projeto não encontrado.")
            ids = [b["id"] for b in project["blocks"]]
            if block_id not in ids:
                raise ValueError("Selecione um trecho.")
            index = ids.index(block_id)
            target = index + direction
            if 0 <= target < len(ids):
                project["blocks"][index], project["blocks"][target] = (
                    project["blocks"][target],
                    project["blocks"][index],
                )
                project["mix"] = None
                self.write(data)
        return self.narration(project_id)

    def set_narration_model(self, project_id, model_project=None, checkpoint=None):
        if model_project and checkpoint not in self.checkpoints(model_project):
            raise ValueError("Escolha um checkpoint do projeto treinado.")
        with self._storage:
            data = self.read()
            project = next((p for p in data.get("narrations", []) if p["id"] == project_id), None)
            if not project:
                raise ValueError("Abra um projeto de narração.")
            for block in project["blocks"]:
                block.update(model_project=model_project or None, checkpoint=checkpoint if model_project else None)
            project["mix"] = None
            self.write(data)
        return self.narration(project_id)

    def set_pronunciations(self, source):
        rules = {}
        for line in source.splitlines():
            if not line.strip():
                continue
            if "=" not in line:
                raise ValueError("Use uma regra por linha: palavra = como ler.")
            word, reading = (part.strip() for part in line.split("=", 1))
            if not word or not reading or len(word) > 100 or len(reading) > 500:
                raise ValueError("Informe uma palavra e uma leitura válidas.")
            if word.casefold() in {key.casefold() for key in rules}:
                raise ValueError("Não repita a mesma palavra no dicionário.")
            rules[word] = reading
        if len(rules) > 100:
            raise ValueError("Use no máximo 100 regras de pronúncia.")
        with self._storage:
            data = self.read()
            data["pronunciations"] = rules
            self.write(data)

    @staticmethod
    def pronounce(text, rules):
        if not rules:
            return text
        normalized = {key.casefold(): value for key, value in rules.items()}
        pattern = (
            r"(?<!\w)(?:" + "|".join(re.escape(word) for word in sorted(rules, key=len, reverse=True)) + r")(?!\w)"
        )
        return re.sub(pattern, lambda match: normalized[match.group(0).casefold()], text, flags=re.IGNORECASE)

    @staticmethod
    def model_version(checkpoint):
        if checkpoint and Path(checkpoint).is_file():
            stat = Path(checkpoint).stat()
            return [stat.st_mtime_ns, stat.st_size]
        return None

    def block_signature(self, block, settings=None, spoken_text=None, model_version=None):
        data = self.read()
        voice = next((v for v in data["voices"] if v["id"] == block["voice"]), None)
        settings = settings or data["settings"]
        payload = {
            "text": block["text"],
            "voice": voice,
            "spoken_text": spoken_text
            if spoken_text is not None
            else self.pronounce(block["text"], data.get("pronunciations", {})),
            "model_project": block.get("model_project"),
            "checkpoint": block.get("checkpoint"),
            "model_version": model_version
            if model_version is not None
            else self.model_version(block.get("checkpoint")),
            "steps": settings["steps"],
            "speed": settings["speed"],
        }
        if block.get("model_project"):
            project = self.project(block["model_project"])
            options = project.read().get("training_options", {})
            vocabulary = options.get("vocabulary") or str(project.path / "vocab.txt")
            payload["vocabulary_version"] = self.model_version(vocabulary)
        if block.get("model_id", "original") != "original":
            model = next((m for m in data.get("models", []) if m["id"] == block["model_id"]), None)
            payload["registered_model"] = model
            payload["model_version"] = (
                model_version
                if model_version is not None
                else self.model_version(model["checkpoint"] if model else None)
            )
            payload["vocabulary_version"] = self.model_version(model["vocabulary"] if model else None)
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def render_block(self, project_id, block_id):
        project = self.narration(project_id)
        block = next((b for b in project["blocks"] if b["id"] == block_id), None)
        if block is None:
            raise ValueError("Selecione um trecho para gerar.")
        entry = self.generate(
            block["text"],
            voice_id=block["voice"],
            project=block.get("model_project"),
            checkpoint=block.get("checkpoint"),
            model_id=block.get("model_id", "original"),
        )
        signature = self.block_signature(
            block,
            {"steps": entry["steps"], "speed": entry["speed"]},
            entry.get("spoken_text", entry["text"]),
            entry.get("model_version"),
        )
        with self._storage:
            data = self.read()
            current = next(p for p in data["narrations"] if p["id"] == project_id)
            current_block = next(b for b in current["blocks"] if b["id"] == block_id)
            current_block["render"] = {"entry": entry["id"], "path": entry["path"], "signature": signature}
            current["mix"] = None
            self.write(data)
        return entry

    def block_ready(self, block):
        return bool(
            block["render"]
            and block["render"]["signature"] == self.block_signature(block)
            and Path(block["render"]["path"]).is_file()
        )

    def stop_render(self):
        self._render_cancel.set()
        return "Parada solicitada. O trecho atual termina antes de parar."

    def render_narration(self, project_id):
        self._render_cancel.clear()
        try:
            blocks = self.narration(project_id)["blocks"]
            for index, block in enumerate(blocks):
                if self._render_cancel.is_set():
                    raise ValueError("Geração interrompida. Os trechos já prontos foram preservados.")
                self.render_status = f"Trecho {index + 1} de {len(blocks)}"
                if not self.block_ready(block):
                    self.render_block(project_id, block["id"])
            if self._render_cancel.is_set():
                raise ValueError("Geração interrompida. Os trechos já prontos foram preservados.")
            self.render_status = "Trechos gerados. Exportando…"
            result = self.export_narration(project_id)
            self.render_status = "Narração pronta."
            return result
        except Exception as error:
            self.render_status = str(error)
            raise

    def export_narration(self, project_id):
        project = self.narration(project_id)
        segments, audio_parts, cursor = [], [], 0
        for index, block in enumerate(project["blocks"]):
            if not self.block_ready(block):
                raise ValueError("Gere novamente os trechos novos ou alterados antes de exportar.")
            audio, rate = sf.read(block["render"]["path"], dtype="float32", always_2d=True)
            mono = torch.from_numpy(audio.mean(axis=1).copy())
            if rate != 24000:
                mono = torchaudio.functional.resample(mono, rate, 24000)
            audio_parts.append(mono.numpy())
            character = next(
                (c["name"] for c in project.get("characters", []) if c["id"] == block.get("character")), None
            )
            caption = f"{character}: {block['text']}" if character else block["text"]
            segments.append({"start": cursor / 24000, "end": (cursor + len(mono)) / 24000, "text": caption})
            cursor += len(mono)
            if index < len(project["blocks"]) - 1:
                pause = np.zeros(round(block["pause"] * 24000), dtype=np.float32)
                audio_parts.append(pause)
                cursor += len(pause)
        if not audio_parts:
            raise ValueError("O projeto não tem trechos.")
        identifier = uuid4().hex
        wav = self.outputs / (identifier + ".wav")
        srt = self.outputs / (identifier + ".srt")
        sf.write(wav, np.concatenate(audio_parts), 24000)
        srt.write_text(subtitles(segments), encoding="utf-8")
        result = {"path": str(wav), "srt": str(srt), "segments": segments}
        with self._storage:
            data = self.read()
            current = next(p for p in data["narrations"] if p["id"] == project_id)
            if (
                current["blocks"] != project["blocks"]
                or current.get("characters") != project.get("characters")
                or not all(self.block_ready(block) for block in current["blocks"])
            ):
                raise ValueError("O roteiro foi alterado durante a exportação. Exporte novamente.")
            current["mix"] = result
            self.write(data)
        return result

    def transcripts(self):
        return self.read().get("transcripts", [])

    def transcript(self, identifier):
        item = next((t for t in self.transcripts() if t["id"] == identifier), None)
        if item is None:
            raise ValueError("Abra uma transcrição existente.")
        return item

    def store_transcript(self, record):
        record = {**record}
        record.setdefault("id", uuid4().hex)
        record["text"] = " ".join(s["text"].strip() for s in record["segments"])
        txt = self.outputs / (record["id"] + ".txt")
        srt = self.outputs / (record["id"] + ".srt")
        txt.write_text(record["text"], encoding="utf-8")
        srt.write_text(subtitles(record["segments"]), encoding="utf-8")
        record.update(txt=str(txt), srt=str(srt))
        with self._storage:
            data = self.read()
            records = data.setdefault("transcripts", [])
            existing = next((t for t in records if t["id"] == record["id"]), None)
            if existing is None:
                records.insert(0, record)
            else:
                existing.clear()
                existing.update(record)
            self.write(data)
        return record

    def edit_transcript(self, identifier, segment_index, text):
        if not text or not text.strip():
            raise ValueError("O texto do trecho não pode ficar vazio.")
        with self._storage:
            record = self.transcript(identifier)
            if not 0 <= int(segment_index) < len(record["segments"]):
                raise ValueError("Trecho inválido.")
            record["segments"][int(segment_index)]["text"] = text.strip()
            return self.store_transcript(record)

    def _release_speech_model(self):
        self._model, self._model_key = None, None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def transcribe_audio(self, source, language="pt", model_id="openai/whisper-small"):
        if model_id not in ASR_MODELS:
            raise ValueError("Escolha um modelo de transcrição da lista.")
        source = str(self.managed_upload(source))
        if language and language not in LANGUAGES:
            raise ValueError("Idioma não suportado pelo seletor.")
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução.")
        try:
            self._release_speech_model()
            asr = getattr(self, "_asr_factory", None)
            if asr is None:
                from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

                offline = bool(self.read().get("offline", False))
                model = AutoModelForSpeechSeq2Seq.from_pretrained(model_id, local_files_only=offline)
                processor = AutoProcessor.from_pretrained(model_id, local_files_only=offline)
                device = self.read()["settings"]["device"]
                cuda = torch.cuda.is_available() and device != "cpu"
                asr = pipeline(
                    "automatic-speech-recognition",
                    model=model,
                    tokenizer=processor.tokenizer,
                    feature_extractor=processor.feature_extractor,
                    device=0 if cuda else -1,
                )
            import librosa

            samples, _ = librosa.load(source, sr=16000, mono=True)
            duration = len(samples) / 16000
            if duration < 0.1:
                raise ValueError("O arquivo não contém áudio suficiente.")
            result = asr(
                {"array": samples, "sampling_rate": 16000},
                chunk_length_s=30,
                batch_size=1,
                return_timestamps=True,
                generate_kwargs={"task": "transcribe", **({"language": language} if language else {})},
            )
            chunks = result.get("chunks") or [{"text": result.get("text", ""), "timestamp": (0, duration)}]
            segments = []
            for chunk in chunks:
                if not chunk["text"].strip():
                    continue
                start, end = chunk.get("timestamp", (0, duration))
                start = max(segments[-1]["end"] if segments else 0, float(start or 0))
                end = min(duration, float(end if end is not None else duration))
                if end > start:
                    segments.append({"start": start, "end": end, "text": chunk["text"].strip()})
            if not segments:
                raise ValueError("Não foi reconhecida fala. Confira o arquivo e o idioma.")
            return self.store_transcript(
                {
                    "source": source,
                    "name": re.sub(r"^[0-9a-f]{32}-", "", Path(source).name),
                    "language": language,
                    "model": model_id,
                    "segments": segments,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        finally:
            self._operation.release()
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def translate_transcript(self, identifier, source_language, target_language):
        if source_language not in LANGUAGES or target_language not in LANGUAGES or source_language == target_language:
            raise ValueError("Escolha idiomas de origem e destino diferentes.")
        record = self.transcript(identifier)
        if not self._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução.")
        try:
            self._release_speech_model()
            translator = getattr(self, "_translation_factory", None)
            if translator is None:
                from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

                offline = bool(self.read().get("offline", False))
                model_id = "facebook/nllb-200-distilled-600M"
                tokenizer = AutoTokenizer.from_pretrained(
                    model_id, src_lang=LANGUAGES[source_language], local_files_only=offline
                )
                model = AutoModelForSeq2SeqLM.from_pretrained(model_id, local_files_only=offline)
                if torch.cuda.is_available() and self.read()["settings"]["device"] != "cpu":
                    model.to("cuda")

                def translator(text):
                    inputs = tokenizer(text, return_tensors="pt", truncation=False).to(model.device)
                    if inputs["input_ids"].shape[1] > 512:
                        raise ValueError("Um trecho excede o limite do tradutor. Divida-o antes de traduzir.")
                    with torch.inference_mode():
                        output = model.generate(
                            **inputs,
                            forced_bos_token_id=tokenizer.convert_tokens_to_ids(LANGUAGES[target_language]),
                            max_new_tokens=512,
                        )
                    return tokenizer.batch_decode(output, skip_special_tokens=True)[0]

            translated = [{**segment, "text": translator(segment["text"])} for segment in record["segments"]]
            return self.store_transcript(
                {
                    "source": record["source"],
                    "name": record["name"] + " · " + target_language,
                    "language": target_language,
                    "model": "facebook/nllb-200-distilled-600M",
                    "segments": translated,
                    "translated_from": identifier,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        finally:
            self._operation.release()
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def set_offline(self, enabled):
        with self._storage:
            data = self.read()
            data["offline"] = bool(enabled)
            self.write(data)

    def process_audio(self, source, effect, output_format="wav"):
        allowed = {
            "normalize": "loudnorm=I=-16:TP=-1.5:LRA=11",
            "noise": "afftdn=nr=12",
            "silence": "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-45dB:stop_periods=-1:stop_duration=0.4:stop_threshold=-45dB",
            "none": "anull",
        }
        if effect not in allowed or output_format not in ("wav", "mp3"):
            raise ValueError("Escolha um efeito e um formato válidos.")
        source = self.managed_upload(source)
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise ValueError("Instale o FFmpeg e adicione-o ao PATH para usar esta ferramenta.")
        output = self.outputs / (uuid4().hex + "." + output_format)
        result = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-af",
                allowed[effect],
                *(["-c:a", "libmp3lame", "-q:a", "2"] if output_format == "mp3" else ["-c:a", "pcm_s16le"]),
                str(output),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError("Não foi possível processar o áudio. Confira o formato e a instalação do FFmpeg.")
        with self._storage:
            data = self.read()
            data.setdefault("processed_audio", []).insert(
                0,
                {
                    "path": str(output),
                    "effect": effect,
                    "format": output_format,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            self.write(data)
        return str(output)
