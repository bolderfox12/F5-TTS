"""Worker entrypoint; runs only in the isolated WhisperX Python environment."""

import copy
import json
import os
import sys
from pathlib import Path


def emit(progress, message):
    print(json.dumps({"progress": progress, "message": message}), flush=True)


def load_checkpoint(request):
    path_value = request.get("checkpoint")
    if not path_value:
        return None
    path = Path(path_value)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and state.get("key") == request.get("cache_key") else None


def save_checkpoint(request, stage, payload):
    path_value = request.get("checkpoint")
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "key": request["cache_key"],
                    "stage": stage,
                    "speaker_count": request.get("speaker_count"),
                    "payload": payload,
                },
                ensure_ascii=False,
                default=lambda value: value.item() if hasattr(value, "item") else str(value),
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
    except (OSError, TypeError, ValueError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        emit(0, f"Ponto de retomada não salvo: {exc}")


def main():
    request = json.load(sys.stdin)
    try:
        checkpoint = load_checkpoint(request)
        if (
            checkpoint
            and checkpoint.get("speaker_count") != request.get("speaker_count")
            and checkpoint.get("stage") in ("turns", "complete")
        ):
            if request.get("diarize_only"):
                checkpoint = None
            else:
                checkpoint = {"stage": "aligned", "payload": checkpoint["payload"]["result"]}
        if checkpoint and checkpoint["stage"] == "complete":
            emit(0.98, "Retomando resultado já concluído…")
            print(json.dumps(checkpoint["payload"], ensure_ascii=False), flush=True)
            return

        import torch
        import whisperx
        from whisperx.diarize import DiarizationPipeline

        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        token = os.getenv("HF_TOKEN") or None
        if not token:
            try:
                from huggingface_hub import get_token

                token = get_token()
            except Exception:
                pass
        if not token:
            raise ValueError(
                "Informe um token Hugging Face e aceite os termos de acesso dos modelos pyannote de diarização "
                "e segmentation-3.0."
            )
        audio = None
        result = None
        stage = checkpoint.get("stage") if checkpoint else None
        payload = checkpoint.get("payload", {}) if checkpoint else {}
        turn_rows = payload.get("turns", []) if stage == "turns" else []
        if stage == "turns" and not request.get("diarize_only"):
            result = payload["result"]
            emit(0.58, "Retomando da diarização concluída…")
        elif stage in ("aligned", "complete") and not request.get("diarize_only"):
            result = payload
            emit(0.45, "Retomando da transcrição alinhada…")
        elif stage == "asr" and not request.get("diarize_only"):
            result = payload
            emit(0.30, "Retomando do alinhamento das palavras…")
        if request.get("diarize_only"):
            audio = whisperx.load_audio(request["audio"])
        elif result is None:
            emit(0.05, f"Carregando WhisperX em {device.upper()}…")
            audio = whisperx.load_audio(request["audio"])
            model = whisperx.load_model(request["model"], device, compute_type=compute_type)
            result = model.transcribe(audio, batch_size=8 if device == "cuda" else 2, language=request["language"])
            result = {"segments": result["segments"], "language": result["language"]}
            save_checkpoint(request, "asr", result)
        if not request.get("diarize_only") and stage not in ("aligned", "turns", "complete"):
            if audio is None:
                audio = whisperx.load_audio(request["audio"])
            emit(0.42, "Alinhando palavras para evitar cortes nas falas…")
            align_model, metadata = whisperx.load_align_model(language_code=result["language"], device=device)
            result = whisperx.align(result["segments"], align_model, metadata, audio, device, return_char_alignments=False)
            result = {"segments": result["segments"], "language": result["language"]}
            save_checkpoint(request, "aligned", result)
        if audio is None and stage != "turns":
            audio = whisperx.load_audio(request["audio"])
        if stage == "turns" and not request.get("diarize_only"):
            import pandas as pd

            turns = pd.DataFrame(turn_rows)
        else:
            emit(0.60, "Identificando personagens com pyannote…")
            diarizer = DiarizationPipeline(token=token, device=device)
            kwargs = {"num_speakers": request["speaker_count"]} if request.get("speaker_count") else {}
            # Feed decoded samples so pyannote does not need torchcodec's FFmpeg shared libraries.
            turns = diarizer(
                audio,
                **kwargs,
                progress_callback=lambda value: emit(0.60 + 0.0035 * value, "Diarizando vozes…"),
            )
            turn_rows = [
                {"start": float(row.start), "end": float(row.end), "speaker": str(row.speaker)}
                for row in turns.itertuples()
            ]
        if request.get("diarize_only"):
            output = {"segments": [], "turns": turn_rows}
            save_checkpoint(request, "complete", output)
            print(json.dumps(output, ensure_ascii=False), flush=True)
            return
        aligned_result = copy.deepcopy(result)
        if stage != "turns":
            save_checkpoint(request, "turns", {"result": aligned_result, "turns": turn_rows})
        result = whisperx.assign_word_speakers(turns, result, fill_nearest=True)
        rows = []
        for segment in result.get("segments", []):
            for word in segment.get("words", []):
                text = word.get("word", "").strip()
                if text and word.get("start") is not None and word.get("end") is not None:
                    rows.append({"start": float(word["start"]), "end": float(word["end"]), "text": text,
                                 "speaker": word.get("speaker") or segment.get("speaker")})
        emit(0.96, "Montando falas curtas por personagem…")
        output = {"segments": rows, "turns": turn_rows, "result": aligned_result}
        save_checkpoint(request, "complete", output)
        print(json.dumps(output, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
        raise


if __name__ == "__main__":
    main()
