"""Worker entrypoint; runs only in the isolated WhisperX Python environment."""

import json
import os
import sys


def emit(progress, message):
    print(json.dumps({"progress": progress, "message": message}), flush=True)


def main():
    request = json.load(sys.stdin)
    try:
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
            raise ValueError("Informe um token Hugging Face e aceite os termos de acesso dos modelos pyannote de diarização e segmentation-3.0.")
        audio = whisperx.load_audio(request["audio"])
        if not request.get("diarize_only"):
            emit(0.05, f"Carregando WhisperX em {device.upper()}…")
            model = whisperx.load_model(request["model"], device, compute_type=compute_type)
            result = model.transcribe(audio, batch_size=8 if device == "cuda" else 2, language=request["language"])
            emit(0.42, "Alinhando palavras para evitar cortes nas falas…")
            align_model, metadata = whisperx.load_align_model(language_code=result["language"], device=device)
            result = whisperx.align(result["segments"], align_model, metadata, audio, device, return_char_alignments=False)
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
            print(json.dumps({"segments": [], "turns": turn_rows}), flush=True)
            return
        result = whisperx.assign_word_speakers(turns, result, fill_nearest=True)
        rows = []
        for segment in result.get("segments", []):
            for word in segment.get("words", []):
                text = word.get("word", "").strip()
                if text and word.get("start") is not None and word.get("end") is not None:
                    rows.append({"start": float(word["start"]), "end": float(word["end"]), "text": text,
                                 "speaker": word.get("speaker") or segment.get("speaker")})
        emit(0.96, "Montando falas curtas por personagem…")
        print(json.dumps({"segments": rows, "turns": turn_rows}), flush=True)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
        raise


if __name__ == "__main__":
    main()
