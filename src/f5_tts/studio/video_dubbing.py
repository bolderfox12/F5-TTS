"""Transcribe, translate and replace the soundtrack of an uploaded video."""

import hashlib
import logging
import math
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from uuid import uuid4

import numpy as np
import soundfile as sf

from f5_tts.studio import chatterbox_backend as chatterbox
from f5_tts.studio.demucs_backend import separate_track
from f5_tts.studio.dubbing_transcript import transcript_warnings
from f5_tts.studio.local_tools import LANGUAGES, subtitles
from f5_tts.studio.script_review import transcribe_gemini_dialogue, translate_gemini_dialogue
from f5_tts.studio.video_probe import VIDEO_INPUT, video_duration
from f5_tts.studio.transcription_gaps import merge_recovered, recovery_windows
from f5_tts.studio.vocal_profiles import estimate_profile, estimate_samples, keep_distinct_profiles


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


def _assign_speakers(embeddings, segments, speaker_count=None, *, profiles=None):
    vectors = np.array(embeddings, dtype=float)
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-8)
    # Longer clips establish speaker identities before short replies are assigned.
    durations = [segment["end"] - segment["start"] for segment in segments]
    order = sorted(range(len(segments)), key=durations.__getitem__, reverse=True)
    if speaker_count is not None:
        from scipy.cluster.hierarchy import cut_tree, linkage
        from scipy.spatial.distance import squareform

        count = min(speaker_count, len(order))
        if count == 1:
            return [1] * len(segments)
        anchors = order[: max(count, sum(duration >= 1 for duration in durations))]
        if profiles:
            for profile in ("masculine", "feminine"):
                match = next((index for index in order if profiles[index] == profile), None)
                if match is not None and match not in anchors:
                    anchors.append(match)
        anchor_vectors = vectors[anchors]
        distances = np.clip(1 - anchor_vectors @ anchor_vectors.T, 0, 2)
        if profiles:
            known = np.array([profiles[index] for index in anchors])
            distances += 3 * ((known[:, None] != known) & (known[:, None] != "unknown") & (known != "unknown"))
        np.fill_diagonal(distances, 0)
        tree = linkage(squareform(distances, checks=False), method="average")
        anchor_labels = cut_tree(tree, n_clusters=count).ravel()
        centers = np.stack([anchor_vectors[anchor_labels == label].mean(axis=0) for label in range(count)])
        centers /= np.maximum(np.linalg.norm(centers, axis=1, keepdims=True), 1e-8)
        labels = (vectors @ centers.T).argmax(axis=1).tolist()
        for index, label in zip(anchors, anchor_labels, strict=True):
            labels[index] = int(label)
        ordered = {label: index for index, label in enumerate(dict.fromkeys(labels), 1)}
        return [ordered[label] for label in labels]

    groups = []
    labels = [0] * len(segments)
    for index in order:
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


def _extract_original_audio(ffmpeg, source, audio):
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


def dubbing_stems(service, ffmpeg, source, *, report=None):
    source = service.managed_upload(source)
    info = source.stat()
    key = hashlib.sha256(f"{source}:{info.st_size}:{info.st_mtime_ns}".encode()).hexdigest()
    cache = service.outputs / "dubbing-stems" / key
    vocals, background = cache / "vocals.wav", cache / "no_vocals.wav"
    if vocals.is_file() and background.is_file():
        if report:
            report(0.14, "Reutilizando a voz e o fundo já separados…")
        return vocals, background
    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="demucs-", dir=cache.parent) as temp:
        work = Path(temp)
        track = work / "source.wav"
        _convert(
            ffmpeg,
            *VIDEO_INPUT,
            "-i",
            source,
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
        separated = separate_track(service, track, work, report=report)
        cache.mkdir(exist_ok=True)
        separated[0].replace(vocals)
        separated[1].replace(background)
    return vocals, background


def check_vocal_profiles(service, source, segments, *, on_progress=None):
    source = service.managed_upload(source)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("Instale o FFmpeg para conferir o áudio original.")
    results = []
    with tempfile.TemporaryDirectory(prefix="f5-vocal-check-") as folder:
        path = Path(folder) / "original.wav"
        _extract_original_audio(ffmpeg, source, path)
        with sf.SoundFile(path) as audio:
            rate = audio.samplerate
            duration = audio.frames / rate
            for index, row in enumerate(segments, 1):
                start, end = row["start"], row["end"]
                profile = "unknown"
                if start is not None and end is not None and 0 <= start < end <= duration + 0.001:
                    first = round(start * rate)
                    remaining = min(audio.frames, round(end * rate)) - first
                    audio.seek(first)
                    detected = set()
                    while remaining >= rate // 3 and len(detected) < 2:
                        frames = min(4 * rate, remaining)
                        samples = audio.read(frames, dtype="float32")
                        remaining -= frames
                        estimate = estimate_samples(samples, rate)
                        if estimate != "unknown":
                            detected.add(estimate)
                    if len(detected) == 1:
                        profile = detected.pop()
                results.append({"start": start, "end": end, "profile": profile})
                if on_progress:
                    on_progress(
                        index / len(segments), "Conferindo fala {done} de {total}…", done=index, total=len(segments)
                    )
    return results


def analyze_video(
    service,
    source,
    source_language,
    model_id,
    *,
    gemini_key="",
    on_progress=None,
    recover_missing=False,
    speaker_count=None,
    separate_vocals=False,
    time_range=None,
):
    def report(value, message):
        if on_progress:
            on_progress(value, message)

    source = service.managed_upload(source)
    if source.suffix.lower() not in VIDEO_EXTENSIONS or source.stat().st_size > VIDEO_LIMIT:
        raise ValueError("Envie um vídeo MP4, MOV, MKV ou WebM de até 500 MB.")
    if source_language not in LANGUAGES:
        raise ValueError("Idioma não suportado pelo seletor.")
    if speaker_count is not None and (type(speaker_count) is not int or speaker_count < 1):
        raise ValueError("Informe um número inteiro de vozes ou deixe a quantidade em branco.")
    offset = 0.0
    selected_duration = None
    if time_range is not None:
        try:
            offset, selected_end = map(float, time_range)
        except (TypeError, ValueError) as error:
            raise ValueError("Selecione um intervalo válido na linha do tempo.") from error
        duration = video_duration(source)
        if not math.isfinite(offset) or not math.isfinite(selected_end) or not 0 <= offset < selected_end <= duration:
            raise ValueError("O intervalo selecionado precisa estar dentro da duração do vídeo.")
        selected_duration = selected_end - offset
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
        python = chatterbox.chatterbox_python(service.home) if speaker_count is not None else None
    else:
        ffmpeg, python = _tools(service)
    directory = Path(tempfile.mkdtemp(prefix="dublagem-", dir=service.uploads))
    try:
        report(0.08, "Extraindo áudio do vídeo…")
        audio = directory / "original.wav"
        if separate_vocals:
            vocals, _ = dubbing_stems(service, ffmpeg, source, report=report)
            _convert(ffmpeg, "-protocol_whitelist", "file", "-i", vocals, "-ac", 1, "-ar", 16000, audio)
        else:
            _extract_original_audio(ffmpeg, source, audio)
        analysis_audio = audio
        if time_range is not None:
            analysis_audio = directory / "selected.wav"
            _convert(
                ffmpeg,
                "-ss",
                f"{offset:.6f}",
                "-i",
                audio,
                "-t",
                f"{selected_duration:.6f}",
                "-ac",
                1,
                "-ar",
                16000,
                analysis_audio,
            )
        if use_gemini:
            report(0.30, "Preparando o áudio para o Gemini…")
            compressed = directory / "gemini.mp3"
            _convert(ffmpeg, "-i", analysis_audio, "-c:a", "libmp3lame", "-b:a", "48k", compressed)
            if compressed.stat().st_size > GEMINI_AUDIO_LIMIT:
                raise ValueError("O áudio excede o limite do Gemini nesta tela. Use um trecho menor ou o Whisper.")
            with wave.open(str(analysis_audio), "rb") as speech:
                duration = speech.getnframes() / speech.getframerate()
            report(0.45, "Aguardando transcrição e personagens do Gemini…")
            encoded = compressed.read_bytes()
            segments = transcribe_gemini_dialogue(
                encoded,
                duration,
                source_language,
                gemini_key,
                allow_empty=recover_missing,
                speaker_count=speaker_count,
            )
            if recover_missing:
                samples, rate = sf.read(analysis_audio, dtype="float32")
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
                            speaker_count=speaker_count,
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
                str(analysis_audio),
                source_language,
                model_id,
                save_record=False,
                return_segments=True,
                recover_missing=recover_missing,
                on_recovery=lambda: report(0.62, "Revisando intervalos sem transcrição…"),
                short_lines=True,
                on_review=lambda: report(0.55, "Rechecando falas com palavras repetidas…"),
            )
        if offset:
            segments = [{**row, "start": row["start"] + offset, "end": row["end"] + offset} for row in segments]
        report(0.65, "Conferindo repetições e trechos silenciosos…")
        warnings = transcript_warnings(segments)
        segments = [row for row, warning in zip(segments, warnings, strict=True) if warning != "duplicate"]
        with sf.SoundFile(audio) as speech:
            audible = []
            for row in segments:
                first = min(speech.frames, max(0, round(row["start"] * speech.samplerate)))
                last = min(speech.frames, round(row["end"] * speech.samplerate))
                speech.seek(first)
                samples = speech.read(max(0, last - first), dtype="float32")
                if samples.size and np.any(np.abs(samples) >= 0.0003):
                    audible.append(row)
        segments = audible
        if not segments:
            raise ValueError("Não foi reconhecida fala no áudio original.")
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
        report(0.82, "Analisando os perfis vocais…")
        profiles = [estimate_profile(sample) for sample in references]
        if use_gemini:
            labels = [segment["speaker"] for segment in segments]
        if not use_gemini or (python is not None and python.is_file()):
            report(0.88, "Identificando personagens pelas vozes…")
            try:
                embeddings = chatterbox.identify_voices(
                    python,
                    references,
                    directory / "vozes.json",
                    offline=bool(service.read().get("offline")),
                )
                labels = _assign_speakers(embeddings, segments, speaker_count, profiles=profiles)
            except (OSError, ValueError):
                if not use_gemini:
                    raise
                logging.getLogger(__name__).warning("Local voice separation unavailable", exc_info=True)
    except Exception:
        shutil.rmtree(directory)
        raise
    audio.unlink()
    (directory / "vozes.json").unlink(missing_ok=True)
    report(0.97, "Preparando os cartões de fala…")
    labels = keep_distinct_profiles(labels, profiles, [segment["end"] - segment["start"] for segment in segments])
    return [
        {**segment, "speaker": label, "reference": sample, "vocal_profile": profile}
        for segment, label, sample, profile in zip(segments, labels, references, profiles, strict=True)
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


def translate_dialogue(
    service, segments, source_language, target_language, *, provider="nllb", gemini_key="", timing_source=None
):
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
    if timing_source is None:
        return translate_gemini_dialogue(segments, source_language, target_language, key)
    if any(
        row["start"] is None
        or row["end"] is None
        or not 0 <= row["start"] < row["end"]
        or not math.isfinite(row["end"])
        for row in segments
    ):
        raise ValueError("Confira o início e o fim de cada fala para adaptar a tradução ao áudio original.")
    source = service.managed_upload(timing_source)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("Instale o FFmpeg para extrair o áudio original.")
    with tempfile.TemporaryDirectory(prefix="traducao-") as directory:
        audio = Path(directory) / "original.mp3"
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
            "-c:a",
            "libmp3lame",
            "-b:a",
            "48k",
            audio,
        )
        if audio.stat().st_size > GEMINI_AUDIO_LIMIT:
            raise ValueError("O áudio excede o limite do Gemini. Use um vídeo mais curto ou traduza apenas o texto.")
        return translate_gemini_dialogue(
            segments, source_language, target_language, key, original_audio=audio.read_bytes()
        )


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
