"""Per-project voice previews and stream-copy export for dubbing review."""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

import soundfile as sf
from pydantic import BaseModel, Field

from f5_tts.studio import chatterbox_backend as chatterbox
from f5_tts.studio.adr_audio import HUMAN_TREATMENT, save_take
from f5_tts.studio.script_review import shorten_dubbing_line
from f5_tts.studio.video_dubbing import _convert, _publish_video, _tools
from f5_tts.studio.video_probe import VIDEO_INPUT, video_media_info


MAX_SPEED = 1.30


class VoiceRequest(BaseModel):
    text: str = Field(min_length=1, max_length=16000)
    speaker: int = Field(ge=1)
    language: str
    model: str = chatterbox.MODEL_ID
    reference_id: UUID | None = None


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
    voice_volume: int = Field(default=100, ge=0, le=200)
    auto_speed: bool = True
    treat_audio: bool = True
    treat_human: bool = True
    latency_ms: int = Field(default=0, ge=-300, le=300)


def demucs_command(service):
    configured = os.environ.get("F5_STUDIO_DEMUCS")
    if configured:
        return shutil.which(configured)
    executable = service.home / "demucs" / ".venv" / ("Scripts/demucs.exe" if os.name == "nt" else "bin/demucs")
    return str(executable) if executable.is_file() else shutil.which("demucs")


class DubbingProject:
    def __init__(self, service, source, segments, duration):
        self.service = service
        self.source = service.managed_upload(source)
        self.duration = duration
        self.id = uuid4()
        self.directory = service.outputs / "dubbing" / self.id.hex
        self.directory.mkdir(parents=True, exist_ok=False)
        self.rows = {uuid4(): dict(segment) for segment in segments}
        self._cache = {}
        self._files = {}
        self._lock = Lock()
        self.takes = {}
        self.active_takes = {}
        self.take_numbers = {}

    def add_line(self, row):
        with self._lock:
            identifier = uuid4()
            self.rows[identifier] = dict(row)
            return identifier

    def attach_reference(self, segment_id, row):
        with self._lock:
            self.rows[segment_id] = dict(row)

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
        if request.language not in chatterbox.LANGUAGES or request.model not in self.service.model_choices():
            raise ValueError("Escolha um modelo e idioma válidos para a dublagem.")
        if request.model == "original" and request.language not in {"en", "zh"}:
            raise ValueError("O F5-TTS original é para inglês e chinês. Escolha outro modelo para este idioma.")
        text = request.text.strip()
        if not text:
            raise ValueError("Preencha a tradução de todos os trechos antes de dublar.")
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

    def _voices(self, requests, on_progress=None):
        pending = [key for key in dict.fromkeys(requests) if key not in self._cache]
        if not pending:
            if on_progress:
                on_progress(1, "Reutilizando os áudios já gerados…" if requests else "Preparando os takes humanos…")
            return [self._cache[key] for key in requests]

        def report(done):
            if on_progress:
                on_progress(done / len(pending), "Áudios gerados: {done} de {total}", done=done, total=len(pending))

        finished = 0
        report(finished)
        service = self.service
        if not service._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução.")
        try:
            with tempfile.TemporaryDirectory(prefix="voices-", dir=self.directory) as folder:
                folder = Path(folder)
                generated = {}
                casts = {}
                for index, key in enumerate(pending):
                    model, language, text, reference, transcript = key
                    if model == chatterbox.MODEL_ID:
                        output = folder / f"{index}.wav"
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
                        )
                        generated[key] = Path(entry["path"])
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
                        )
                        finished += sum(len(lines) for lines in cast.values())
                        report(finished)
                for key, output in generated.items():
                    duration = sf.info(output).duration
                    if duration <= 0:
                        raise ValueError("Um trecho da dublagem foi gerado sem fala audível.")
                    target = self.directory / f"{uuid4().hex}.wav"
                    output.replace(target)
                    self._files[target.name] = target
                    self._cache[key] = (target, duration)
        finally:
            if service.generation_status()["running"]:
                service.set_generation_status(False, 100, "Geração concluída.")
            service._operation.release()
        return [self._cache[key] for key in requests]

    def regenerate(self, identifier, request, *, on_progress=None):
        with self._lock:
            key = self._request(identifier, request)
            previous = self._cache.pop(key, None)
            try:
                return self._voices([key], on_progress)[0]
            except Exception:
                if previous:
                    self._cache[key] = previous
                raise

    def prepare(self, lines, *, on_progress=None):
        with self._lock:
            keys = [self._request(line.id, line) for line in lines if line.take_id is None]
            return self._voices(keys, on_progress)

    def fit_preview(self, request, originals, gemini_key, *, on_progress=None):
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
            ai_lines, on_progress=lambda value, message, **values: report(0.05 + 0.25 * value, message, **values)
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
            candidates[line.id] = [line.model_copy(update={"text": option}) for option in options[:2]]

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
                on_progress=lambda value, _message, **_values: report(
                    0.60 + (round_index + value) * 0.18, "Conferindo a duração das falas encurtadas…"
                ),
            )
            for line, (_, duration) in zip(batch, measured, strict=True):
                if duration < clip_lengths[line.id] and duration / ceiling - slots[line.id] <= 0.001:
                    accepted[line.id] = line
        return (
            request.model_copy(update={"lines": [accepted.get(line.id, line) for line in request.lines]})
            if accepted
            else request
        )

    def export(self, request, *, preview=False, on_progress=None):
        def report(value, message, **values):
            if on_progress:
                on_progress(value, message, **values)

        with self._lock:
            report(0.05, "Conferindo os tempos e o roteiro…")
            return self._export(request, preview=preview, report=report)

    def _background(self, ffmpeg, directory):
        output = self.directory / "background.wav"
        if output.is_file():
            return output
        if not video_media_info(self.source)[3]:
            silence = directory / "silence.wav"
            _convert(ffmpeg, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", self.duration, silence)
            silence.replace(output)
            return output
        command = demucs_command(self.service)
        if not command:
            raise ValueError("Instale o Demucs para preservar música e efeitos ou deixe o fundo em 0%.")
        weights = self.service.home / "demucs" / "weights"
        if (
            self.service.read().get("offline")
            and not (weights / "hub" / "checkpoints" / "955717e8-8726e21a.th").is_file()
        ):
            raise ValueError("Baixe os pesos do Demucs antes de separar o fundo no modo offline.")
        track = directory / "source.wav"
        _convert(
            ffmpeg,
            *VIDEO_INPUT,
            "-i",
            self.source,
            "-map",
            "0:a:0",
            "-af",
            "aresample=async=1:first_pts=0",
            "-ar",
            44100,
            "-ac",
            2,
            track,
        )
        try:
            subprocess.run(
                [command, "--two-stems", "vocals", "-n", "htdemucs", "-d", "cpu", "-o", str(directory), str(track)],
                check=True,
                capture_output=True,
                timeout=3600,
                env={**os.environ, "TORCH_HOME": str(weights), "OMP_NUM_THREADS": "4"},
            )
            (directory / "htdemucs" / "source" / "no_vocals.wav").replace(output)
        except (subprocess.SubprocessError, OSError) as error:
            logging.getLogger(__name__).error("Demucs failed: %s", getattr(error, "stderr", None) or error)
            raise ValueError(
                "O Demucs falhou ao separar o áudio de fundo. Confira a instalação e os pesos do modelo."
            ) from error
        return output

    def _export(self, request, *, preview, report):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError("Instale o FFmpeg para dublar vídeos.")
        identifiers = [line.id for line in request.lines]
        if len(identifiers) != len(self.rows) or set(identifiers) != self.rows.keys():
            raise ValueError("Envie uma vez cada fala deste projeto.")
        rows = sorted(request.lines, key=lambda line: line.start)
        cursor = 0
        windows = []
        for line in rows:
            start, end = round(line.start * 1000), min(round(line.end * 1000), round(self.duration * 1000))
            if start < cursor or end <= start or line.end > self.duration + 0.001:
                raise ValueError("Ajuste os tempos das falas. Use intervalos válidos e sem sobreposição.")
            windows.append((start, end))
            cursor = end
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
            self._voices(keys, lambda value, message, **values: report(0.10 + 0.33 * value, message, **values))
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
            chain.extend(
                (f"atrim=duration={length:.3f}", f"volume={request.voice_volume / 100}", f"adelay={start}:all=1")
            )
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
            if request.background_volume:
                report(0.53, "Separando música e efeitos do áudio original…")
                background = self._background(ffmpeg, directory)
                inputs.extend(("-i", background))
                filters.append(f"[{len(rows)}:a]aresample=48000,volume={request.background_volume / 100}[bg]")
                tracks.append("[bg]")
            report(0.68, "Mixando voz dublada e fundo…")
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
