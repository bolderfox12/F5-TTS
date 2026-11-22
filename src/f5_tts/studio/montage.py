"""Persistent Studio montage projects and multi-track FFmpeg rendering."""

import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import soundfile as sf

from f5_tts.studio.local_tools import subtitles
from f5_tts.studio.montage_fx import validate_video_effects, video_effect_filters
from f5_tts.studio.video_dubbing import VIDEO_EXTENSIONS, _convert, _publish_video
from f5_tts.studio.video_editor import MUSIC_EXTENSIONS, MUSIC_INPUT
from f5_tts.studio.video_probe import VIDEO_INPUT, video_media_info

TRACKS = {"video": ("V1", "V2"), "audio": ("A1", "A2"), "caption": ("CC",), "text": ("TX",)}
PROFILES = {"720p": (1280, 720), "1080p": (1920, 1080), "vertical": (720, 1280)}
PITCH_LIMIT = 48
EQ_LIMIT = 12
EQ_BANDS = {
    "low": ("Graves", "bass=f=150:width_type=o:width=1"),
    "mid": ("Médios", "equalizer=f=1200:width_type=o:width=2"),
    "high": ("Agudos", "treble=f=4000:width_type=o:width=1"),
}
SRT_TIMING = re.compile(r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})")


def asset_kind(path):
    extension = Path(path).suffix.lower()
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in MUSIC_EXTENSIONS:
        return "audio"
    if extension == ".srt":
        return "caption"
    return None


def parse_srt(path):
    def seconds(groups):
        hours, minutes, whole, milliseconds = map(int, groups)
        return hours * 3600 + minutes * 60 + whole + milliseconds / 1000

    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        match = SRT_TIMING.search(block)
        if not match:
            continue
        start, end = seconds(match.groups()[:4]), seconds(match.groups()[4:])
        text = block[match.end() :].partition("\n")[2].strip()
        if text and end > start:
            cues.append({"start": start, "end": end, "text": text})
    if not cues:
        raise ValueError("Não foi possível ler as legendas SRT.")
    return sorted(cues, key=lambda cue: cue["start"])


class MontageStore:
    def __init__(self, service):
        self.service = service

    def list(self):
        return sorted(self.service.read().get("video_projects", []), key=lambda project: project["name"].casefold())

    def open(self, identifier):
        project = next((row for row in self.list() if row["id"] == identifier), None)
        if project is None:
            raise ValueError("Projeto de vídeo não encontrado.")
        return project

    def create(self, name, assets=()):
        project = {
            "id": uuid4().hex,
            "name": name.strip()[:100] or "Meu vídeo",
            "revision": 0,
            "profile": "720p",
            "clips": [],
        }
        return self._append_and_save(project, assets)

    def transfer(self, identifier, name, assets):
        if identifier:
            return self._append_and_save(self.open(identifier), assets)
        return self.create(name, assets)

    def _append_and_save(self, project, assets):
        offset = max((clip["start"] + clip["out"] - clip["in"] for clip in project["clips"]), default=0)
        for path, name in assets:
            project["clips"].extend(self.import_clips(path, name, offset))
        return self.save(project)

    def save(self, project):
        if project["profile"] not in PROFILES:
            raise ValueError("Escolha um formato de vídeo disponível.")
        with self.service._storage:
            data = self.service.read()
            projects = data.setdefault("video_projects", [])
            current = next((item for item in projects if item["id"] == project["id"]), None)
            if current is not None and current["revision"] != project["revision"]:
                raise ValueError("O projeto mudou em outra janela. Reabra o projeto para continuar.")
            updated = {**project, "revision": project["revision"] + 1}
            if current is not None:
                projects[projects.index(current)] = updated
            else:
                projects.append(updated)
            self.service.write(data)
        return updated

    def inventory(self):
        entries = {item["path"]: item for item in self.service.asset_inventory() if asset_kind(item["path"])}
        for root in (self.service.uploads, self.service.outputs):
            for path in root.rglob("*"):
                if asset_kind(path) == "video" and path.is_file():
                    entries[str(path.resolve())] = {
                        "path": str(path.resolve()),
                        "name": path.name,
                        "category": "Vídeo",
                        "uses": [],
                        "modified": path.stat().st_mtime,
                    }
        return sorted(entries.values(), key=lambda row: row["modified"], reverse=True)

    def clip(self, source, name, position):
        path = self.service._managed_asset(source)
        kind = asset_kind(path)
        if kind == "video":
            duration, _, _, has_audio = video_media_info(path)
        elif kind == "audio":
            try:
                duration, has_audio = sf.info(path).duration, True
            except (RuntimeError, TypeError):
                duration, has_audio = self._probe_audio(path), True
        elif kind == "caption":
            duration, has_audio = max(cue["end"] for cue in parse_srt(path)), False
        else:
            raise ValueError("Escolha um vídeo, áudio ou arquivo SRT do Studio.")
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Não foi possível ler a duração do arquivo.")
        return {
            "id": uuid4().hex,
            "path": str(path),
            "name": name.strip()[:140] or path.name,
            "kind": kind,
            "track": TRACKS[kind][0],
            "duration": duration,
            "has_audio": has_audio,
            "start": round(position, 3),
            "in": 0.0,
            "out": duration,
            "volume": 100,
        }

    def import_clips(self, source, name, position):
        return separate_video_audio(self.clip(source, name, position))

    @staticmethod
    def _probe_audio(path):
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise ValueError("Instale o FFprobe para analisar vídeos.")
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    *MUSIC_INPUT,
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                text=True,
                capture_output=True,
                timeout=30,
                check=True,
            )
            return float(result.stdout.strip())
        except (ValueError, subprocess.SubprocessError) as error:
            raise ValueError("Não foi possível ler a duração do arquivo.") from error


def audio_tracks(project):
    tracks = {"A1", "A2", *project.get("audio_tracks", ())}
    tracks.update(clip["track"] for clip in project["clips"] if clip["kind"] == "audio")
    return sorted(tracks, key=lambda track: int(track[1:]))


def new_audio_track(project):
    tracks = audio_tracks(project)
    return f"A{int(tracks[-1][1:]) + 1}"


def sequence_duration(clips):
    return max((row["start"] + row["out"] - row["in"] for row in clips), default=0)


def validate_clip(clip):
    start, first, last, duration = (clip[key] for key in ("start", "in", "out", "duration"))
    if not all(isinstance(value, (float, int)) and math.isfinite(value) for value in (start, first, last, duration)):
        raise ValueError("Informe tempos válidos para o clipe.")
    if start < 0 or first < 0 or last - first < 0.01 or last > duration + 0.0005:
        raise ValueError("O corte precisa estar dentro da duração do clipe.")
    valid_track = (
        re.fullmatch(r"A[1-9]\d*", clip["track"]) if clip["kind"] == "audio" else clip["track"] in TRACKS[clip["kind"]]
    )
    if not valid_track or not 0 <= clip["volume"] <= 200:
        raise ValueError("Confira a faixa e o volume do clipe.")
    if clip["kind"] == "video":
        validate_video_effects(clip.get("video_effects", {}))
    pitch = clip.get("pitch", 0)
    if not isinstance(pitch, (float, int)) or not -PITCH_LIMIT <= pitch <= PITCH_LIMIT:
        raise ValueError("Escolha um pitch entre -48 e +48 semitons.")
    equalizer = clip.get("equalizer", {})
    if not isinstance(equalizer, dict) or any(
        key not in EQ_BANDS or not isinstance(gain, (int, float)) or not -EQ_LIMIT <= gain <= EQ_LIMIT
        for key, gain in equalizer.items()
    ):
        raise ValueError("Ajuste o equalizador entre -12 e +12 dB por faixa.")
    if clip["kind"] == "text" and (not clip.get("text", "").strip() or not 12 <= clip.get("font_size", 48) <= 150):
        raise ValueError("Preencha o texto e escolha um tamanho de fonte válido.")


def separate_video_audio(clip):
    if clip["kind"] != "video" or not clip["has_audio"]:
        return [clip]
    group = uuid4().hex
    sound = {
        **clip,
        "id": uuid4().hex,
        "kind": "audio",
        "track": "A1",
        "embedded_audio": True,
        "link": group,
        "name": f"{clip['name']} · Áudio",
    }
    return [{**clip, "has_audio": False, "link": group}, sound]


def linked_clips(clips, clip):
    group = clip.get("link")
    return [row for row in clips if row.get("link") == group] if group else [clip]


def split_at(clips, clip, position):
    members = linked_clips(clips, clip)
    split = round(position - clip["start"] + clip["in"], 3)
    if min(split - clip["in"], clip["out"] - split) < 0.01:
        raise ValueError("Posicione o cursor dentro do clipe para dividir.")
    right_link = uuid4().hex if clip.get("link") else None
    by_id = {row["id"]: row for row in members}
    result, selected = [], None
    for row in clips:
        if row["id"] not in by_id:
            result.append(row)
            continue
        point = round(position - row["start"] + row["in"], 3)
        left = {**row, "out": point}
        right = {**row, "id": uuid4().hex, "in": point, "start": row["start"] + point - row["in"], "link": right_link}
        validate_clip(left)
        validate_clip(right)
        result.extend((left, right))
        if row["id"] == clip["id"]:
            selected = right["id"]
    return result, selected


def duplicate_clip(clips, clip):
    members = linked_clips(clips, clip)
    length = clip["out"] - clip["in"]
    group = uuid4().hex if clip.get("link") else None
    copies = [({**row, "id": uuid4().hex, "start": round(row["start"] + length, 3), "link": group}) for row in members]
    return [*clips, *copies], copies[members.index(clip)]["id"]


def _read_cues(path, clip):
    offset = clip["start"] - clip["in"]
    return [
        {
            "start": max(row["start"], clip["in"]) + offset,
            "end": min(row["end"], clip["out"]) + offset,
            "text": row["text"],
        }
        for row in parse_srt(path)
        if min(row["end"], clip["out"]) > max(row["start"], clip["in"])
    ]


def _supports_rubberband(ffmpeg):
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=10, check=True
        )
        return any(line.split()[1:2] == ["rubberband"] for line in result.stdout.splitlines())
    except (OSError, subprocess.SubprocessError):
        return False


def audio_effect_filters(clip, *, rubberband=False):
    filters = []
    pitch = clip.get("pitch", 0)
    if pitch and rubberband:
        filters.extend(("apad=pad_dur=0.15", f"rubberband=pitch={2 ** (pitch / 12):.8f}"))
    elif pitch:
        rate = round(48000 * 2 ** (pitch / 12))
        filters.extend((f"asetrate={rate}", "aresample=48000"))
        tempo = 48000 / rate
        while tempo > 2:
            filters.append("atempo=2")
            tempo /= 2
        while tempo < 0.5:
            filters.append("atempo=0.5")
            tempo *= 2
        if tempo != 1:
            filters.append(f"atempo={tempo:.8f}")
    equalizer = clip.get("equalizer", {})
    for band, (_, shape) in EQ_BANDS.items():
        gain = equalizer.get(band, 0)
        if gain:
            filters.append(f"{shape}:g={gain:.4f}")
    return filters


def render_montage(service, project, *, preview=False):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("Instale o FFmpeg para editar vídeos.")
    clips = project["clips"]
    for clip in clips:
        validate_clip(clip)
    length = sequence_duration(clips)
    if not clips or length < 0.01 or all(clip["kind"] == "caption" for clip in clips):
        raise ValueError("Adicione um vídeo ou áudio à linha do tempo.")
    width, height = PROFILES["720p" if preview and project["profile"] == "1080p" else project["profile"]]
    outputs = service.outputs / "video-editor" / project["id"]
    outputs.mkdir(parents=True, exist_ok=True)
    target = outputs / f"{uuid4().hex}-{'previa' if preview else 'montagem'}.mp4"
    media = [clip for clip in clips if clip["kind"] in {"video", "audio"}]
    rubberband = any(clip.get("pitch") and clip["has_audio"] for clip in media) and _supports_rubberband(ffmpeg)
    with tempfile.TemporaryDirectory(prefix="montagem-", dir=outputs) as temporary:
        directory = Path(temporary)
        inputs, graph, audio, video_layers, cues = [], [], [], [], []
        for index, clip in enumerate(media):
            path = service._managed_asset(clip["path"])
            source_kind = "video" if clip.get("embedded_audio") else clip["kind"]
            if asset_kind(path) != source_kind:
                raise ValueError("O formato do clipe não corresponde à faixa.")
            source_options = VIDEO_INPUT if source_kind == "video" else MUSIC_INPUT
            trim = clip["out"] - clip["in"]
            inputs.extend((*source_options, "-ss", clip["in"], "-t", trim, "-i", path))
            if clip["kind"] == "video":
                video_layers.append((index, clip))
            if clip["has_audio"] and clip["volume"]:
                filter_id = f"a{index}"
                effects = audio_effect_filters(clip, rubberband=rubberband)
                chain = [
                    f"atrim=duration={trim:.6f}",
                    "asetpts=PTS-STARTPTS",
                    "aresample=48000",
                    *effects,
                    f"atrim=duration={trim:.6f}",
                    "aformat=channel_layouts=stereo",
                    f"volume={clip['volume'] / 100:.4f}",
                    f"adelay={round(clip['start'] * 1000)}:all=1",
                ]
                graph.append(f"[{index}:a:0]{','.join(chain)}[{filter_id}]")
                audio.append(f"[{filter_id}]")
        graph.append(f"color=c=0x102632:s={width}x{height}:r=30:d={length:.6f}[base]")
        video = "base"
        for index, clip in sorted(video_layers, key=lambda pair: (pair[1]["track"], pair[1]["start"])):
            inset = clip["track"] == "V2"
            w, h = (width // 3 // 2 * 2, height // 3 // 2 * 2) if inset else (width, height)
            chain = [
                f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2",
                *video_effect_filters(clip.get("video_effects", {}), directory, index),
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
                "setsar=1",
                "fps=30",
                f"setpts=PTS-STARTPTS+{clip['start']:.6f}/TB",
            ]
            graph.append(f"[{index}:v:0]{','.join(chain)}[v{index}]")
            placement = "W-w-24:H-h-24" if inset else "0:0"
            last = clip["start"] + clip["out"] - clip["in"]
            graph.append(
                f"[{video}][v{index}]overlay={placement}:eof_action=pass:repeatlast=0:"
                f"enable='gte(t,{clip['start']:.6f})*lt(t,{last:.6f})'[next{index}]"
            )
            video = f"next{index}"
        for index, clip in enumerate(row for row in clips if row["kind"] == "text"):
            text_path = f"text-{index}.txt"
            (directory / text_path).write_text(clip["text"], encoding="utf-8")
            start = clip["start"]
            end = start + clip["out"] - clip["in"]
            graph.append(
                f"[{video}]drawtext=textfile={text_path}:expansion=none:fontcolor=white:"
                f"fontsize={int(clip.get('font_size', 48))}:borderw=3:"
                f"x=(w-tw)/2:y=(h-th)/2:enable='gte(t,{start:.6f})*lt(t,{end:.6f})'[text{index}]"
            )
            video = f"text{index}"
        for clip in clips:
            if clip["kind"] == "caption":
                cues.extend(_read_cues(service._managed_asset(clip["path"]), clip))
        cues.sort(key=lambda row: row["start"])
        if cues:
            (directory / "captions.srt").write_text(subtitles(cues), encoding="utf-8")
            graph.append(f"[{video}]subtitles=captions.srt[captioned]")
            video = "captioned"
        if audio:
            graph.append(
                f"{''.join(audio)}amix=inputs={len(audio)}:normalize=0:dropout_transition=0,"
                "alimiter=limit=0.89:level=0:latency=1,apad[sound]"
            )
        else:
            graph.append("anullsrc=r=48000:cl=stereo[sound]")
        script = directory / "filters.txt"
        script.write_text(";\n".join(graph), encoding="utf-8")
        draft = directory / target.name
        _convert(
            ffmpeg,
            "-filter_complex_threads",
            2,
            *inputs,
            "-filter_complex_script",
            script,
            "-map",
            f"[{video}]",
            "-map",
            "[sound]",
            "-t",
            length,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast" if preview else "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            48000,
            "-movflags",
            "+faststart",
            draft,
            cwd=directory,
        )
        return _publish_video(draft, target, cues)
