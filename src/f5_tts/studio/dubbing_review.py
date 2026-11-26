"""Per-project voice previews and stream-copy export for dubbing review."""

import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

import soundfile as sf
from pydantic import BaseModel, Field

from f5_tts.studio import chatterbox_backend as chatterbox, gemini_tts, google_cloud_tts
from f5_tts.studio.adr_audio import HUMAN_TREATMENT, Take, save_take
from f5_tts.studio.dubbing_transcript import repeated_text
from f5_tts.studio.script_review import shorten_dubbing_line
from f5_tts.studio.music_library import resolve_music
from f5_tts.studio.video_dubbing import _convert, _publish_video, _tools, dubbing_stems
from f5_tts.studio.video_probe import VIDEO_INPUT, video_media_info


MAX_SPEED = 1.30
GEMINI_VOICE_MODELS = (gemini_tts.MODEL_ID, google_cloud_tts.MODEL_ID)


class VoiceRequest(BaseModel):
    text: str = Field(min_length=1, max_length=16000)
    speaker: int = Field(ge=1)
    language: str
    model: str = chatterbox.MODEL_ID
    reference_id: UUID | None = None
    gemini_voice: str | None = None
    gemini_style: str = Field(default="", max_length=500)


class ReviewLine(VoiceRequest):
    text: str = Field(default="", max_length=16000)
    take_id: UUID | None = None
    offset_ms: int = Field(default=0, ge=-300, le=300)
    id: UUID
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)


class ExportRequest(BaseModel):
    lines: list[ReviewLine] = Field(min_length=1)
    background_volume: int = Field(default=0, ge=0, le=100)
    background_music_id: str | None = None
    background_music_volume: int = Field(default=20, ge=0, le=100)
    auto_duck: bool = True
    voice_volume: int = Field(default=100, ge=0, le=200)
    auto_speed: bool = True
    treat_audio: bool = True
    treat_human: bool = True
    latency_ms: int = Field(default=0, ge=-300, le=300)


class DubbingProject:
    def __init__(self, service, source, segments, duration, *, project_id=None):
        self.service = service
        self.source = service.managed_upload(source)
        self.duration = duration
        self.id = project_id or uuid4()
        self.directory = service.outputs / "dubbing" / self.id.hex
        self.directory.mkdir(parents=True, exist_ok=project_id is not None)
        self.rows = {uuid4(): dict(segment) for segment in segments}
        self._cache = {}
        self._files = {}
        self._active_cloud_voice = None
        self.voice_revision = 0
        self._lock = Lock()
        self.takes = {}
        self.active_takes = {}
        self.take_numbers = {}

    def draft_state(self):
        with self._lock:
            return {
                "id": str(self.id),
                "duration": self.duration,
                "row_ids": [str(identifier) for identifier in self.rows],
                "takes": [
                    {**asdict(take), "segment_id": str(segment_id), "id": str(take.id), "path": str(take.path)}
                    for segment_id, recordings in self.takes.items()
                    for take in recordings.values()
                ],
                "active_takes": [[str(segment), str(take)] for segment, take in self.active_takes.items()],
                "take_numbers": [[str(segment), number] for segment, number in self.take_numbers.items()],
            }

    @classmethod
    def restore_draft(cls, service, source, rows, state):
        project = cls(service, source, [], state["duration"], project_id=UUID(state["id"]))
        for row in rows:
            if row.get("reference"):
                row["reference"] = service.managed_upload(row["reference"])
        project.rows = {UUID(identifier): dict(row) for identifier, row in zip(state["row_ids"], rows, strict=True)}
        for item in state["takes"]:
            segment_id, take_id = UUID(item["segment_id"]), UUID(item["id"])
            path = Path(item["path"]).resolve()
            if not path.is_relative_to(project.directory.resolve()) or not path.is_file():
                continue
            take = Take(take_id, path, item["duration"], item["capture_offset_ms"], item["number"])
            project.takes.setdefault(segment_id, {})[take_id] = take
            project._files[path.name] = path
        for identifier, selected in state["active_takes"]:
            segment_id, take_id = UUID(identifier), UUID(selected)
            if take_id in project.takes.get(segment_id, {}):
                project.active_takes[segment_id] = take_id
        project.take_numbers = {UUID(identifier): number for identifier, number in state["take_numbers"]}
        return project

    def add_line(self, row):
        with self._lock:
            identifier = uuid4()
            self.rows[identifier] = dict(row)
            return identifier

    def attach_reference(self, segment_id, row):
        with self._lock:
            self.rows[segment_id] = dict(row)

    def remove_line(self, segment_id):
        with self._lock:
            if self.takes.get(segment_id):
                raise ValueError("Remova as gravações desta fala antes de excluí-la.")
            self.rows.pop(segment_id)
            self.active_takes.pop(segment_id, None)
            self.take_numbers.pop(segment_id, None)
            return True

    def store_take(self, segment_id, source, media_type, capture_offset):
        with self._lock:
            if segment_id not in self.rows:
                raise ValueError("Esta fala não pertence ao projeto.")
            number = self.take_numbers.get(segment_id, 0) + 1
            take = save_take(
                self.directory / "takes" / segment_id.hex,
                source,
                media_type,
                capture_offset,
                number,
            )
            self.take_numbers[segment_id] = number
            self.takes.setdefault(segment_id, {})[take.id] = take
            self._files[take.path.name] = take.path
            self.active_takes[segment_id] = take.id
            return take

    def choose_take(self, segment_id, take_id):
        with self._lock:
            if segment_id not in self.rows or (take_id is not None and take_id not in self.takes.get(segment_id, {})):
                raise ValueError("Take não encontrado nesta fala.")
            if take_id is None:
                self.active_takes.pop(segment_id, None)
            else:
                self.active_takes[segment_id] = take_id

    def forget_take(self, segment_id, take_id):
        with self._lock:
            take = self.takes.get(segment_id, {}).get(take_id)
            if not take:
                raise ValueError("Take não encontrado nesta fala.")
            take.path.unlink(missing_ok=True)
            self.takes[segment_id].pop(take_id)
            self._files.pop(take.path.name, None)
            if self.active_takes.get(segment_id) == take_id:
                self.active_takes.pop(segment_id)

    def guide(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError("Instale o FFmpeg para dublar vídeos.")
        with self._lock, tempfile.TemporaryDirectory(prefix="guide-", dir=self.directory) as folder:
            result = self._background(ffmpeg, Path(folder))
            self._files[result.name] = result
            return result

    def _request(self, identifier, request):
        if identifier not in self.rows:
            raise ValueError("Esta fala não pertence ao projeto.")
        if request.language not in chatterbox.LANGUAGES or (
            request.model not in GEMINI_VOICE_MODELS and request.model not in self.service.model_choices()
        ):
            raise ValueError("Escolha um modelo e idioma válidos para a dublagem.")
        if request.model == "original" and request.language not in {"en", "zh"}:
            raise ValueError("O F5-TTS original é para inglês e chinês. Escolha outro modelo para este idioma.")
        text = request.text.strip()
        if not text:
            raise ValueError("Preencha a tradução de todos os trechos antes de dublar.")
        if request.model in GEMINI_VOICE_MODELS:
            if request.model == google_cloud_tts.MODEL_ID and request.language not in google_cloud_tts.LOCALES:
                raise ValueError("Escolha um idioma disponível no Google Cloud Text-to-Speech.")
            voice = request.gemini_voice or gemini_tts.default_voice(request.speaker)
            if voice not in gemini_tts.VOICES:
                raise ValueError("Escolha uma voz Gemini TTS disponível.")
            return request.model, request.language, text, voice, request.gemini_style.strip()
        reference = self.rows.get(request.reference_id or identifier)
        if reference is None:
            raise ValueError("A referência de voz não pertence ao projeto.")
        if request.reference_id is None and (reference["speaker"] != request.speaker or not reference.get("reference")):
            reference = next(
                (row for row in self.rows.values() if row["speaker"] == request.speaker and row.get("reference")),
                None,
            )
            if reference is None:
                raise ValueError("Selecione uma referência para este personagem.")
        path = reference.get("reference")
        if path is None:
            raise ValueError("Selecione uma referência para este personagem.")
        return request.model, request.language, text, path, reference.get("reference_transcript", reference["text"])

    def _voice_cache_path(self, key):
        digest = hashlib.sha256(json.dumps(key, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
        prefix = "cloud" if key[0] in GEMINI_VOICE_MODELS else "voice"
        return self.directory / f"{prefix}-{digest}.wav"

    def _cloud_voice_path(self, key):
        return self._voice_cache_path(key)

    def _load_cloud_voice(self, key):
        path = self._cloud_voice_path(key)
        if not path.is_file():
            return None
        try:
            duration = sf.info(path).duration
        except (OSError, RuntimeError):
            return None
        if duration <= 0:
            return None
        self._files[path.name] = path
        return path, duration

    def cloud_voice(self, identifier, request):
        key = self._request(identifier, request)
        return self._load_cloud_voice(key), self._active_cloud_voice == key

    def _publish_voice(self, key, output):
        duration = sf.info(output).duration
        if duration <= 0:
            raise ValueError("Um trecho da dublagem foi gerado sem fala audível.")
        target = self._voice_cache_path(key)
        if Path(output) != target:
            Path(output).replace(target)
        self._files[target.name] = target
        self._cache[key] = (target, duration)

    def _voices(self, requests, on_progress=None, *, gemini_key="", force=False, cancel_event=None):
        from f5_tts.studio.chatterbox_backend import SynthesisCancelled

        def check_cancelled():
            if cancel_event is not None and cancel_event.is_set():
                raise SynthesisCancelled

        check_cancelled()
        if not force:
            for key in dict.fromkeys(requests):
                cached = self._load_cloud_voice(key)
                if cached:
                    self._cache[key] = cached
                else:
                    self._cache.pop(key, None)
        pending = [key for key in dict.fromkeys(requests) if key not in self._cache]
        unique_count = len(dict.fromkeys(requests))
        reused_count = unique_count - len(pending)
        if not pending:
            if on_progress:
                on_progress(1, "Reutilizando os áudios já gerados…" if requests else "Preparando os takes humanos…")
            return [self._cache[key] for key in requests]

        def report(done):
            if on_progress:
                ready = reused_count + done
                on_progress(
                    ready / max(1, unique_count),
                    "Áudios prontos: {done} de {total}",
                    done=ready,
                    total=unique_count,
                )

        finished = 0
        if reused_count and on_progress:
            on_progress(
                reused_count / max(1, unique_count),
                "Retomando: {done} fala(s) concluída(s) serão reutilizadas.",
                done=reused_count,
            )
        report(finished)
        service = self.service
        models = {key[0] for key in pending}
        if models.intersection(GEMINI_VOICE_MODELS) and service.read().get("offline"):
            raise ValueError("O modo offline está ativo. Desative-o para gerar voz na nuvem.")
        if gemini_tts.MODEL_ID in models:
            gemini_key = gemini_key.strip() or service.review_api_key("gemini")
            if not gemini_key:
                raise ValueError("Informe a chave do Gemini para gerar o áudio.")
        if not service._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução.")
        try:
            cloud_auth = google_cloud_tts.credentials(service) if google_cloud_tts.MODEL_ID in models else None
            with tempfile.TemporaryDirectory(prefix="voices-", dir=self.directory) as folder:
                folder = Path(folder)
                generated = {}
                casts = {}
                for index, key in enumerate(pending):
                    check_cancelled()
                    model, language, text, reference, transcript = key
                    if model in GEMINI_VOICE_MODELS:
                        output = folder / f"{index}.wav"
                        self._active_cloud_voice = key
                        self.voice_revision += 1
                        try:
                            if model == google_cloud_tts.MODEL_ID:
                                google_cloud_tts.synthesize(text, language, reference, transcript, cloud_auth, output)
                            else:
                                gemini_tts.synthesize(text, reference, transcript, gemini_key, output)
                            self._publish_voice(key, output)
                        finally:
                            self._active_cloud_voice = None
                            self.voice_revision += 1
                        finished += 1
                        report(finished)
                    elif model == chatterbox.MODEL_ID:
                        output = folder / f"{index}.wav" if force else self._voice_cache_path(key)
                        output.parent.mkdir(parents=True, exist_ok=True)
                        casts.setdefault(language, {}).setdefault(reference, []).append((text, output))
                        generated[key] = output
                    else:
                        entry = service.generate(
                            text,
                            str(reference),
                            transcript if sf.info(reference).duration < 12 else "",
                            model_id=model,
                            language=language,
                            _part_directory=folder,
                            _part_progress=(index, len(pending)),
                            cancel_event=cancel_event,
                        )
                        generated[key] = Path(entry["path"])
                        self._publish_voice(key, generated[key])
                        generated[key] = self._voice_cache_path(key)
                        finished += 1
                        report(finished)
                if casts:
                    _, python = _tools(service)
                    service._release_speech_model()
                    data = service.read()
                    for language, cast in casts.items():
                        chatterbox.synthesize_cast(
                            python,
                            list(cast.items()),
                            language,
                            data["settings"]["device"],
                            offline=bool(data.get("offline")),
                            on_progress=lambda count, previous=finished: report(previous + count),
                            cancel_event=cancel_event,
                        )
                        finished += sum(len(lines) for lines in cast.values())
                        report(finished)
                for key, output in generated.items():
                    self._publish_voice(key, output)
        finally:
            if service.generation_status()["running"]:
                service.set_generation_status(False, 100, "Geração concluída.")
            service._operation.release()
        return [self._cache[key] for key in requests]

    def regenerate(self, identifier, request, *, on_progress=None, gemini_key="", cancel_event=None):
        with self._lock:
            key = self._request(identifier, request)
            previous = self._cache.pop(key, None)
            try:
                return self._voices([key], on_progress, gemini_key=gemini_key, force=True, cancel_event=cancel_event)[0]
            except Exception:
                if previous:
                    self._cache[key] = previous
                raise

    def prepare(self, lines, *, on_progress=None, gemini_key="", cancel_event=None):
        with self._lock:
            keys = [self._request(line.id, line) for line in lines if line.take_id is None]
            return self._voices(keys, on_progress, gemini_key=gemini_key, cancel_event=cancel_event)

    def fit_preview(self, request, originals, gemini_key, *, on_progress=None, cancel_event=None):
        """Keep alternate text only after its synthetic audio fits the video slot."""
        if not any(line.take_id is None for line in request.lines):
            return request
        if self.service.read().get("offline"):
            raise ValueError("O modo offline está ativo. Desative-o para pedir sugestões ao Gemini.")
        key = gemini_key.strip() or self.service.review_api_key("gemini")
        if not key:
            raise ValueError("Informe a chave da API do provedor escolhido.")

        def report(value, message, **values):
            if on_progress:
                on_progress(value, message, **values)

        ordered = sorted(request.lines, key=lambda line: line.start)
        ai_lines = [line for line in ordered if line.take_id is None]
        voices = self.prepare(
            ai_lines,
            on_progress=lambda value, message, **values: report(0.05 + 0.25 * value, message, **values),
            gemini_key=key,
            cancel_event=cancel_event,
        )
        clip_lengths = {line.id: clip[1] for line, clip in zip(ai_lines, voices, strict=True)}
        ceiling = MAX_SPEED if request.auto_speed else 1
        slots = {line.id: (round(line.end * 1000) - round(line.start * 1000)) / 1000 for line in ai_lines}
        overflows = [
            (index, line)
            for index, line in enumerate(ordered)
            if line.id in slots and slots[line.id] > 0 and clip_lengths[line.id] / ceiling - slots[line.id] > 0.001
        ]
        candidates = {}
        for number, (index, line) in enumerate(overflows, 1):
            report(
                0.30 + 0.30 * (number - 1) / len(overflows),
                "Encurtando fala {done} de {total} com Gemini…",
                done=number,
                total=len(overflows),
            )
            context = [ordered[i].text for i in (index - 1, index + 1) if 0 <= i < len(ordered)]
            options = shorten_dubbing_line(
                self.service,
                originals.get(line.id, ""),
                line.text,
                line.language,
                slots[line.id],
                clip_lengths[line.id],
                gemini_key=key,
                context=context,
            )
            candidates[line.id] = [
                line.model_copy(update={"text": option}) for option in options if not repeated_text(option)
            ][:2]

        accepted = {}
        for round_index in range(2):
            batch = [
                options[round_index]
                for identifier, options in candidates.items()
                if identifier not in accepted and len(options) > round_index
            ]
            if not batch:
                continue
            measured = self.prepare(
                batch,
                gemini_key=key,
                on_progress=lambda value, _message, **_values: report(
                    0.60 + (round_index + value) * 0.18, "Conferindo a duração das falas encurtadas…"
                ),
                cancel_event=cancel_event,
            )
            for line, (_, duration) in zip(batch, measured, strict=True):
                if duration < clip_lengths[line.id] and duration / ceiling - slots[line.id] <= 0.001:
                    accepted[line.id] = line
        return (
            request.model_copy(update={"lines": [accepted.get(line.id, line) for line in request.lines]})
            if accepted
            else request
        )

    def export(self, request, *, preview=False, on_progress=None, gemini_key="", cancel_event=None):
        def report(value, message, **values):
            if on_progress:
                on_progress(value, message, **values)

        with self._lock:
            report(0.05, "Conferindo os tempos e o roteiro…")
            return self._export(request, preview=preview, report=report, gemini_key=gemini_key, cancel_event=cancel_event)

    def _background(self, ffmpeg, directory, *, cancel_event=None):
        output = self.directory / "background.wav"
        if output.is_file():
            return output
        if not video_media_info(self.source)[3]:
            silence = directory / "silence.wav"
            _convert(ffmpeg, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", self.duration, silence)
            silence.replace(output)
            return output
        return dubbing_stems(self.service, ffmpeg, self.source, cancel_event=cancel_event)[1]

    def _export(self, request, *, preview, report, gemini_key="", cancel_event=None):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError("Instale o FFmpeg para dublar vídeos.")
        identifiers = [line.id for line in request.lines]
        if not identifiers or len(set(identifiers)) != len(identifiers) or not set(identifiers) <= self.rows.keys():
            raise ValueError("Envie falas válidas deste projeto, sem duplicá-las.")
        rows = sorted(request.lines, key=lambda line: line.start)
        windows = []
        for line in rows:
            start, end = round(line.start * 1000), min(round(line.end * 1000), round(self.duration * 1000))
            if start < 0 or end <= start or line.end > self.duration + 0.001:
                raise ValueError("Ajuste o início e o fim de cada fala para dentro do vídeo.")
            windows.append((start, end))
        keys = []
        takes = {}
        for line in rows:
            take_id = line.take_id if "take_id" in line.model_fields_set else self.active_takes.get(line.id)
            if take_id is None:
                keys.append(self._request(line.id, line))
            else:
                take = self.takes.get(line.id, {}).get(take_id)
                if take is None:
                    raise ValueError("Take não encontrado nesta fala.")
                takes[line.id] = take
        ai_voices = iter(
            self._voices(
                keys,
                lambda value, message, **values: report(0.10 + 0.33 * value, message, **values),
                gemini_key=gemini_key,
                cancel_event=cancel_event,
            )
        )
        report(0.46, "Sincronizando as falas com o vídeo…")
        captions = []
        inputs, filters, tracks = [], [], []
        for index, (line, window) in enumerate(zip(rows, windows, strict=True)):
            start, end = window
            take = takes.get(line.id)
            if take:
                path, duration = take.path, take.duration
                delay = start + request.latency_ms + line.offset_ms + take.capture_offset_ms
                chain = ["aresample=48000"]
                if request.treat_human:
                    chain.extend(HUMAN_TREATMENT)
                if delay < 0:
                    if -delay / 1000 >= duration:
                        raise ValueError("O atraso ajustado deixa uma gravação inteira antes do início do vídeo.")
                    chain.extend((f"atrim=start={-delay / 1000:.3f}", "asetpts=PTS-STARTPTS"))
                chain.extend((f"volume={request.voice_volume / 100}", f"adelay={max(0, delay)}:all=1"))
                inputs.extend(("-i", path))
                filters.append(f"[{index}:a]{','.join(chain)}[v{index}]")
                tracks.append(f"[v{index}]")
                if line.text.strip():
                    captions.append(
                        {
                            "segment_id": str(line.id),
                            "start": start / 1000,
                            "end": end / 1000,
                            "text": line.text.strip(),
                        }
                    )
                continue
            path, duration = next(ai_voices)
            length = (end - start) / 1000
            speed = duration / length
            maximum_speed = MAX_SPEED if request.auto_speed else 1
            remaining = duration / maximum_speed - length
            if remaining > 0.001 and not preview:
                raise ValueError(
                    f"Fala em {start / 1000:.3f} s excede a janela em {duration - length:.2f} s. "
                    "Encurte o texto, amplie o intervalo ou ative o ajuste automático."
                )
            chain = ["aresample=48000"]
            if request.treat_audio:
                chain.extend(("highpass=f=75", "acompressor=threshold=0.125:ratio=2:attack=12:release=120"))
            if request.auto_speed and speed > 1:
                chain.append(f"atempo={min(speed, MAX_SPEED):.8f}")
            if not (preview and remaining > 0.001):
                chain.append(f"atrim=duration={length:.3f}")
            chain.extend((f"volume={request.voice_volume / 100}", f"adelay={start}:all=1"))
            inputs.extend(("-i", path))
            filters.append(f"[{index}:a]{','.join(chain)}[v{index}]")
            tracks.append(f"[v{index}]")
            captions.append(
                {
                    "segment_id": str(line.id),
                    "start": start / 1000,
                    "end": end / 1000,
                    "text": line.text.strip(),
                    "preview_overflow": preview and remaining > 0.001,
                }
            )

        with tempfile.TemporaryDirectory(prefix="export-", dir=self.directory) as folder:
            directory = Path(folder)
            music_path = resolve_music(self.service, request.background_music_id) if request.background_music_id else None
            voice_tracks = list(tracks)
            background_track = None
            if request.background_volume:
                report(0.53, "Separando música e efeitos do áudio original…")
                background = self._background(ffmpeg, directory, cancel_event=cancel_event)
                inputs.extend(("-i", background))
                background_track = f"[{len(rows)}:a]aresample=48000,volume={request.background_volume / 100}[bg]"
                filters.append(background_track)
                tracks.append("[bg]")
            if music_path:
                music_index = len(rows) + int(bool(request.background_volume))
                inputs.extend(("-stream_loop", "-1", "-i", music_path))
                if request.auto_duck and voice_tracks:
                    filters.append(
                        f"{''.join(voice_tracks)}amix=inputs={len(voice_tracks)}:normalize=0:dropout_transition=0[voicebus]"
                    )
                    filters.append(
                        f"[{music_index}:a]aresample=48000,volume={request.background_music_volume / 100:.3f}[music];"
                        "[music][voicebus]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=450[ducked]"
                    )
                    tracks = ["[voicebus]"] + (["[bg]"] if request.background_volume else []) + ["[ducked]"]
                else:
                    filters.append(
                        f"[{music_index}:a]aresample=48000,volume={request.background_music_volume / 100:.3f}[music]"
                    )
                    tracks.append("[music]")
            report(0.68, "Mixando voz dublada e fundo…")
            if cancel_event is not None and cancel_event.is_set():
                from f5_tts.studio.chatterbox_backend import SynthesisCancelled

                raise SynthesisCancelled
            filters.append(
                f"{''.join(tracks)}amix=inputs={len(tracks)}:normalize=0:dropout_transition=0,"
                "alimiter=limit=0.89:level=0:latency=1,apad[mix]"
            )
            graph = directory / "mix.txt"
            graph.write_text(";\n".join(filters), encoding="utf-8")
            mixed = directory / "mix.m4a"
            _convert(
                ffmpeg,
                "-filter_complex_threads",
                2,
                *inputs,
                "-filter_complex_script",
                graph,
                "-map",
                "[mix]",
                "-t",
                self.duration,
                "-ar",
                48000,
                "-ac",
                2,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                mixed,
                cancel_event=cancel_event,
            )
            report(0.88, "Renderizando a prévia dublada…" if preview else "Exportando o vídeo final…")
            identifier = uuid4().hex

            def mux(extension):
                variant = "previa-dublado" if preview else "dublado"
                draft = directory / f"{identifier}-{variant}.{extension}"
                video_options = (
                    (
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-pix_fmt",
                        "yuv420p",
                        "-vf",
                        "scale=w='min(1280,iw)':h=-2:force_divisible_by=2,pad=ceil(iw/2)*2:ceil(ih/2)*2",
                    )
                    if preview
                    else ("-c:v", "copy")
                )
                flags = ("-movflags", "+faststart") if extension == "mp4" else ()
                _convert(
                    ffmpeg,
                    *VIDEO_INPUT,
                    "-i",
                    self.source,
                    "-i",
                    mixed,
                    "-map",
                    "0:V:0",
                    "-map",
                    "1:a:0",
                    "-t",
                    self.duration,
                    *video_options,
                    "-c:a",
                    "copy",
                    *flags,
                    draft,
                    cancel_event=cancel_event,
                )
                return draft

            try:
                draft = mux("mp4")
            except ValueError:
                if preview:
                    raise
                draft = mux("mkv")
            video, srt = _publish_video(draft, self.directory / draft.name, captions)
        self._files.update({video.name: video, srt.name: srt})
        report(1, "Prévia dublada pronta." if preview else "Vídeo dublado pronto para baixar.")
        return video, srt, captions

    def url(self, path):
        return f"/api/dubbing/{self.id}/files/{path.name}"
