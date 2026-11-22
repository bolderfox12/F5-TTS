"""Transcribe, translate and replace the soundtrack of an uploaded video."""

import logging
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from uuid import uuid4

import numpy as np
import soundfile as sf

from f5_tts.studio import chatterbox_backend as chatterbox
from f5_tts.studio.local_tools import LANGUAGES, subtitles
from f5_tts.studio.script_review import transcribe_gemini_dialogue, translate_gemini_dialogue
from f5_tts.studio.video_probe import video_duration
from f5_tts.studio.transcription_gaps import merge_recovered, recovery_windows


VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")
VIDEO_LIMIT = 500_000_000
GEMINI_TRANSCRIPTION = "gemini"
GEMINI_AUDIO_LIMIT = 14_000_000
DUB_SAMPLE_RATE = 24000
SYNC_REVIEW_SPEED = 1.30


def _convert(ffmpeg, *arguments, cwd=None):
    try:
        subprocess.run(
            [ffmpeg, "-nostdin", "-loglevel", "error", "-y", *map(str, arguments)],
            check=True,
            capture_output=True,
            timeout=1800,
            cwd=cwd,
        )
    except subprocess.CalledProcessError as error:
        logging.getLogger(__name__).error("FFmpeg: %s", error.stderr.decode(errors="replace"))
        raise ValueError("Não foi possível processar o vídeo ou a faixa de áudio com FFmpeg.") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError("O processamento do vídeo excedeu o tempo limite.") from error


def _speech_bounds(clip):
    samples, rate = sf.read(clip, dtype="float32", always_2d=True)
    envelope = np.max(np.abs(samples), axis=1)
    peak = float(envelope.max(initial=0))
    if not np.isfinite(peak) or peak < 0.0001:
        raise ValueError("Um trecho da dublagem foi gerado sem fala audível.")
    active = np.flatnonzero(envelope >= peak * 0.012)
    margin = round(rate * 0.045)
    first = max(0, active[0] - margin)
    last = min(len(samples), active[-1] + 1 + margin)
    return first / rate, last / rate


def _assemble_track(ffmpeg, segments, clips, directory, duration, *, treat_audio):
    track = directory / "dub.wav"
    fitted = directory / "fitted.wav"
    aligned = []
    cursor = 0
    with wave.open(str(track), "wb") as output:
        output.setparams((1, 2, DUB_SAMPLE_RATE, 0, "NONE", "not compressed"))
        for index, (segment, clip) in enumerate(zip(segments, clips, strict=True)):
            start = max(cursor, round(segment["start"] * DUB_SAMPLE_RATE))
            end = round(segment["end"] * DUB_SAMPLE_RATE)
            head, tail = _speech_bounds(clip)
            speech_duration = tail - head
            next_start = segments[index + 1]["start"] if index + 1 < len(segments) else duration
            # Use a short part of the following pause before increasing the speaking rate.
            original_window = (end - start) / DUB_SAMPLE_RATE
            if speech_duration > original_window:
                room = max(0, min(0.28, next_start - segment["end"] - 0.045))
                added = min(speech_duration - original_window, room)
                end += round(added * DUB_SAMPLE_RATE)
            window = (end - start) / DUB_SAMPLE_RATE
            if window <= 0:
                raise ValueError("Revise os tempos do roteiro antes de gerar a dublagem.")
            speed = max(1, speech_duration / window)
            aligned.append(
                {
                    **segment,
                    "start": start / DUB_SAMPLE_RATE,
                    "end": end / DUB_SAMPLE_RATE,
                    "speed": speed,
                }
            )
            filters = [f"atrim=start={head:.8f}:end={tail:.8f}", "asetpts=PTS-STARTPTS"]
            if treat_audio:
                filters.extend(("highpass=f=75", "acompressor=threshold=0.125:ratio=2:attack=12:release=120"))
            # Chain tempo filters to avoid skipping samples at very high speeds.
            while speed > 2:
                filters.append("atempo=2")
                speed /= 2
            if speed > 1:
                filters.append(f"atempo={speed:.8f}")
            fades = min(0.008, window / 4)
            speech_end = min(window, speech_duration)
            filters.extend(
                (
                    f"afade=t=in:d={fades:.8f}",
                    f"afade=t=out:st={speech_end - fades:.8f}:d={fades:.8f}",
                    "apad",
                )
            )
            _convert(
                ffmpeg,
                "-i",
                clip,
                "-af",
                ",".join(filters),
                "-t",
                window,
                "-ac",
                1,
                "-ar",
                DUB_SAMPLE_RATE,
                "-c:a",
                "pcm_s16le",
                fitted,
            )
            while cursor < start:
                frames = min(start - cursor, DUB_SAMPLE_RATE)
                output.writeframesraw(bytes(frames * 2))
                cursor += frames
            with wave.open(str(fitted), "rb") as speech:
                while cursor < end:
                    frames = min(end - cursor, DUB_SAMPLE_RATE)
                    samples = speech.readframes(frames)
                    output.writeframesraw(samples.ljust(frames * 2, b"\0"))
                    cursor += frames
    return track, aligned


def _tools(service):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("Instale o FFmpeg para dublar vídeos.")
    python = chatterbox.chatterbox_python(service.home)
    if not python.is_file():
        raise ValueError("Instale o ambiente do Chatterbox seguindo o guia do Studio.")
    return ffmpeg, python


def _assign_speakers(embeddings, segments):
    vectors = np.asarray(embeddings)
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-8)
    groups = []
    labels = [0] * len(segments)
    # Longer clips establish speaker identities before short replies are assigned.
    durations = [segment["end"] - segment["start"] for segment in segments]
    for index in sorted(range(len(segments)), key=durations.__getitem__, reverse=True):
        vector = vectors[index]
        similarities = [float(vector @ (group / max(np.linalg.norm(group), 1e-8))) for group in groups]
        match = int(np.argmax(similarities)) if similarities else 0
        if not groups or (durations[index] >= 1 and similarities[match] < 0.72):
            match = len(groups)
            groups.append(vector.copy())
        elif durations[index] >= 1:
            groups[match] += vector
        labels[index] = match + 1
    ordered = {label: index for index, label in enumerate(dict.fromkeys(labels), 1)}
    return [ordered[label] for label in labels]


def analyze_video(
    service,
    source,
    source_language,
    model_id,
    *,
    gemini_key="",
    on_progress=None,
    recover_missing=False,
):
    def report(value, message):
        if on_progress:
            on_progress(value, message)

    source = service.managed_upload(source)
    if source.suffix.lower() not in VIDEO_EXTENSIONS or source.stat().st_size > VIDEO_LIMIT:
        raise ValueError("Envie um vídeo MP4, MOV, MKV ou WebM de até 500 MB.")
    if source_language not in LANGUAGES:
        raise ValueError("Idioma não suportado pelo seletor.")
    use_gemini = model_id == GEMINI_TRANSCRIPTION
    if use_gemini:
        if service.read().get("offline"):
            raise ValueError("O modo offline está ativo. Desative-o para transcrever com Gemini.")
        gemini_key = gemini_key.strip() or service.review_api_key("gemini")
        if not gemini_key:
            raise ValueError("Informe a chave da API do provedor escolhido.")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise ValueError("Instale o FFmpeg para dublar vídeos.")
    else:
        ffmpeg, python = _tools(service)
    directory = Path(tempfile.mkdtemp(prefix="dublagem-", dir=service.uploads))
    try:
        report(0.08, "Extraindo áudio do vídeo…")
        audio = directory / "original.wav"
        _convert(
            ffmpeg,
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "mov,matroska,webm",
            "-i",
            source,
            "-map",
            "0:a:0",
            "-af",
            "aresample=async=1:first_pts=0",
            "-vn",
            "-ac",
            1,
            "-ar",
            16000,
            audio,
        )
        if use_gemini:
            report(0.30, "Preparando o áudio para o Gemini…")
            compressed = directory / "gemini.mp3"
            _convert(ffmpeg, "-i", audio, "-c:a", "libmp3lame", "-b:a", "48k", compressed)
            if compressed.stat().st_size > GEMINI_AUDIO_LIMIT:
                raise ValueError("O áudio excede o limite do Gemini nesta tela. Use um trecho menor ou o Whisper.")
            with wave.open(str(audio), "rb") as speech:
                duration = speech.getnframes() / speech.getframerate()
            report(0.45, "Aguardando transcrição e personagens do Gemini…")
            encoded = compressed.read_bytes()
            segments = transcribe_gemini_dialogue(
                encoded,
                duration,
                source_language,
                gemini_key,
                allow_empty=recover_missing,
            )
            if recover_missing:
                samples, rate = sf.read(audio, dtype="float32")
                windows = recovery_windows(samples, rate, segments)
                if windows:
                    report(0.62, "Revisando intervalos sem transcrição…")
                    try:
                        additions = transcribe_gemini_dialogue(
                            encoded,
                            duration,
                            source_language,
                            gemini_key,
                            recovery=(windows, segments),
                        )
                        segments = merge_recovered(segments, additions, windows)
                    except ValueError:
                        logging.getLogger(__name__).warning("Gemini gap recovery failed", exc_info=True)
            compressed.unlink()
            if not segments:
                raise ValueError("Não foi reconhecida fala. Confira o arquivo e o idioma.")
        else:
            report(0.36, "Transcrevendo com Whisper…")
            segments = service._transcribe_audio(
                str(audio),
                source_language,
                model_id,
                save_record=False,
                return_segments=True,
                recover_missing=recover_missing,
                on_recovery=lambda: report(0.62, "Revisando intervalos sem transcrição…"),
            )
        report(0.72, "Extraindo as referências de voz…")
        references = []
        with wave.open(str(audio), "rb") as speech:
            rate = speech.getframerate()
            for index, segment in enumerate(segments):
                sample = directory / f"fala-{index + 1}.wav"
                speech.setpos(round(segment["start"] * rate))
                length = min(12, segment["end"] - segment["start"])
                frames = speech.readframes(max(1, round(length * rate)))
                with wave.open(str(sample), "wb") as output:
                    output.setparams(speech.getparams())
                    output.writeframes(frames.ljust(rate * speech.getsampwidth(), b"\0"))
                references.append(sample)
        if use_gemini:
            labels = [segment["speaker"] for segment in segments]
        else:
            report(0.88, "Identificando personagens pelas vozes…")
            embeddings = chatterbox.identify_voices(
                python,
                references,
                directory / "vozes.json",
                offline=bool(service.read().get("offline")),
            )
            labels = _assign_speakers(embeddings, segments)
    except Exception:
        shutil.rmtree(directory)
        raise
    audio.unlink()
    (directory / "vozes.json").unlink(missing_ok=True)
    report(0.97, "Preparando os cartões de fala…")
    return [
        {**segment, "speaker": label, "reference": sample}
        for segment, label, sample in zip(segments, labels, references, strict=True)
    ]


def speaker_references(segments):
    def sample_length(row):
        return row.get("reference_duration") or ((row.get("end") or 0) - (row.get("start") or 0))

    references = {}
    for segment in sorted(segments, key=lambda row: row.get("preferred_reference", False), reverse=True):
        speaker = segment["speaker"]
        current = references.get(speaker)
        if current is None or (
            not current.get("preferred_reference") and sample_length(segment) > sample_length(current)
        ):
            references[speaker] = segment
    return dict(sorted(references.items()))


def translate_dialogue(service, segments, source_language, target_language, *, provider="nllb", gemini_key=""):
    if not segments or any(not segment["text"].strip() for segment in segments):
        raise ValueError("Preencha a transcrição de todos os trechos antes de dublar.")
    if provider == "nllb":
        return [row["text"] for row in service.translate_segments(segments, source_language, target_language)]
    if provider != "gemini":
        raise ValueError("Escolha um modelo de tradução disponível.")
    if source_language not in LANGUAGES or target_language not in LANGUAGES or source_language == target_language:
        raise ValueError("Escolha idiomas de origem e destino diferentes.")
    if service.read().get("offline"):
        raise ValueError("O modo offline está ativo. Desative-o para traduzir com Gemini.")
    key = gemini_key.strip() or service.review_api_key("gemini")
    if not key:
        raise ValueError("Informe a chave da API do provedor escolhido.")
    return translate_gemini_dialogue(segments, source_language, target_language, key)


def dub_video(service, source, segments, dialogue, target_language, model_id, *, treat_audio=True):
    source = service.managed_upload(source)
    ffmpeg, python = _tools(service)
    if any(segment["speaker"] < 1 for segment in segments):
        raise ValueError("Informe um número de personagem maior que zero.")
    if model_id not in service.model_choices():
        raise ValueError("Selecione um modelo de dublagem existente.")
    if model_id == "original" and target_language not in {"en", "zh"}:
        raise ValueError("O F5-TTS original é para inglês e chinês. Escolha outro modelo para este idioma.")
    if not segments or len(segments) != len(dialogue) or any(not text.strip() for text in dialogue):
        raise ValueError("Preencha a tradução de todos os trechos antes de dublar.")
    duration = video_duration(source)
    translated = [
        {**segment, "text": text.strip(), "end": min(segment["end"], duration)}
        for segment, text in zip(segments, dialogue, strict=True)
        if min(segment["end"], duration) - segment["start"] >= 1 / DUB_SAMPLE_RATE
    ]
    if not translated:
        raise ValueError("Revise os tempos do roteiro antes de gerar a dublagem.")

    data = service.read()
    identifier = uuid4().hex
    output = service.outputs / f"{identifier}-dublado.mp4"
    with tempfile.TemporaryDirectory(prefix="dublagem-", dir=service.outputs) as temporary:
        directory = Path(temporary)
        clips = [directory / f"{index}.wav" for index in range(len(translated))]
        references = speaker_references(segments)
        if not service._operation.acquire(blocking=False):
            raise ValueError("Aguarde a operação em execução.")
        try:
            if model_id == chatterbox.MODEL_ID:
                service._release_speech_model()
                cast = {speaker: [] for speaker in references}
                for segment, clip in zip(translated, clips, strict=True):
                    cast[segment["speaker"]].append((segment["text"], clip))
                chatterbox.synthesize_cast(
                    python,
                    [(segment["reference"], cast[speaker]) for speaker, segment in references.items()],
                    target_language,
                    data["settings"]["device"],
                    offline=bool(data.get("offline")),
                )
            else:
                clips = []
                for index, segment in enumerate(translated):
                    reference = references[segment["speaker"]]
                    transcript = reference["text"] if reference["end"] - reference["start"] <= 12 else ""
                    entry = service.generate(
                        segment["text"],
                        str(reference["reference"]),
                        transcript,
                        model_id=model_id,
                        language=target_language,
                        _part_directory=directory,
                        _part_progress=(index, len(translated)),
                    )
                    clips.append(Path(entry["path"]))
        finally:
            if model_id != chatterbox.MODEL_ID and service.generation_status()["running"]:
                service.set_generation_status(False, 100, "Geração concluída.")
            service._operation.release()
        track, aligned = _assemble_track(ffmpeg, translated, clips, directory, duration, treat_audio=treat_audio)
        audio_filter = "highpass=f=55,loudnorm=I=-17:TP=-1.5:LRA=7,apad" if treat_audio else "apad"
        result = directory / output.name
        _convert(
            ffmpeg,
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "mov,matroska,webm",
            "-i",
            source,
            "-i",
            track,
            "-map",
            "0:V:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-af",
            audio_filter,
            "-ar",
            48000,
            "-shortest",
            "-movflags",
            "+faststart",
            result,
        )
        return (*_publish_video(result, output, aligned), aligned)


def _publish_video(result, output, segments):
    captions = output.with_suffix(".srt")
    staged_captions = result.with_suffix(".srt")
    staged_captions.write_text(subtitles(segments) if segments else "", encoding="utf-8")
    staged_captions.replace(captions)
    try:
        result.replace(output)
    except OSError:
        captions.unlink(missing_ok=True)
        raise
    return output, captions
