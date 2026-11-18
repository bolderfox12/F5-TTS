"""Guided fine-tuning UI and reviewed audio datasets, without automatic training."""

import hashlib
import json
import math
import re
import subprocess
import tempfile
import threading
import unicodedata
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from uuid import uuid4

import gradio as gr
import soundfile as sf
import torch
import torchaudio

from f5_tts.audio import load_audio, local_audio_input, save_audio


_LOCK = threading.RLock()


def training_upload_name(filename):
    path = Path(filename)
    stem = "".join(char if char.isalnum() or char in "-_." else "_" for char in path.stem)[:60]
    return stem + path.suffix.lower()


class TrainingProject:
    def __init__(self, root, name):
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,59}", name):
            raise ValueError("Use de 1 a 60 letras sem acento, números, hífen ou sublinhado no nome.")
        self.name = name if name.endswith("_pinyin") else name + "_pinyin"
        self.path = Path(root).resolve() / self.name
        if not self.path.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError("Pasta de projeto inválida.")
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "wavs").mkdir(exist_ok=True)

    def read(self):
        path = self.path / "reviewed_examples.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"examples": [], "feedback": []}

    def write(self, data):
        temporary = self.path / ("manifest-" + uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path / "reviewed_examples.json")

    def invalidate(self, data):
        data.pop("prepared", None)
        for name in ("raw.arrow", "duration.json"):
            (self.path / name).unlink(missing_ok=True)

    @staticmethod
    def text(value):
        value = unicodedata.normalize("NFC", " ".join(str(value).split()))
        if len(value) < 3 or "|" in value:
            raise ValueError("Informe o texto exato do áudio, com pelo menos 3 caracteres e sem o símbolo |.")
        return value

    def add(self, audio_path, text, consent, source=None):
        if not consent:
            raise ValueError("Confirme que pode usar esta gravação para treinamento.")
        if not audio_path:
            raise ValueError("Grave ou envie um áudio primeiro.")
        text = self.text(text)
        info = sf.info(audio_path)
        if not 1 <= info.duration <= 30:
            raise ValueError("Cada exemplo precisa ter entre 1 e 30 segundos. Prefira trechos de 3 a 12 segundos.")
        waveform, rate = load_audio(audio_path)
        waveform = waveform.mean(dim=0, keepdim=True)
        rms = waveform.square().mean().sqrt().item()
        if not torch.isfinite(waveform).all() or rms < 0.001:
            raise ValueError("O áudio está vazio, muito baixo ou contém valores inválidos. Grave novamente.")
        if rate != 24000:
            waveform = torchaudio.functional.resample(waveform, rate, 24000)
        waveform = waveform.contiguous()
        digest = hashlib.sha256(waveform.numpy().tobytes()).hexdigest()
        quality = {
            "rms_db": round(20 * math.log10(rms), 1),
            "clipped_percent": round(100 * (waveform.abs() >= 0.999).float().mean().item(), 2),
        }
        identifier = uuid4().hex
        with _LOCK:
            data = self.read()
            if source and any(example.get("audio_digest") == digest for example in data["examples"]):
                raise ValueError("Este trecho já existe no projeto.")
            save_audio(self.path / "wavs" / (identifier + ".wav"), waveform, 24000)
            data["examples"].append(
                {
                    "id": identifier,
                    "text": text,
                    "duration": info.duration,
                    "approved": False,
                    "source": source or Path(audio_path).name,
                    "audio_digest": digest,
                    **quality,
                }
            )
            self.invalidate(data)
            self.write(data)
        return self.rows()

    def import_transcripts(self, paths, manifest, consent):
        if not consent or not paths:
            raise ValueError("Selecione gravações e confirme a autorização de uso.")
        transcripts = {}
        for line in manifest.splitlines():
            if not line.strip():
                continue
            filename, separator, text = line.partition("|")
            filename = training_upload_name(filename.strip())
            if not separator or not filename or filename in transcripts:
                raise ValueError("Use uma linha única no formato arquivo.wav|transcrição para cada gravação.")
            transcripts[filename] = self.text(text)

        filenames = [Path(path).name.split("-", 1)[-1] for path in paths]
        if len(filenames) != len(set(filenames)) or set(filenames) != set(transcripts):
            raise ValueError("Os nomes do manifesto precisam corresponder aos arquivos selecionados, sem duplicatas.")

        added, errors = 0, []
        for path, filename in zip(paths, filenames, strict=True):
            try:
                try:
                    self.add(path, transcripts[filename], consent, source=filename)
                except sf.LibsndfileError:
                    with tempfile.TemporaryDirectory() as folder:
                        decoded = Path(folder) / "audio.wav"
                        subprocess.run(
                            [
                                "ffmpeg",
                                "-v",
                                "error",
                                *local_audio_input(path),
                                "-map",
                                "0:a:0",
                                "-t",
                                "31",
                                "-ac",
                                "1",
                                "-ar",
                                "24000",
                                str(decoded),
                            ],
                            check=True,
                            capture_output=True,
                            timeout=60,
                        )
                        self.add(decoded, transcripts[filename], consent, source=filename)
                added += 1
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                errors.append(f"{filename}: {error}")
        return f"{added} trechos aguardando revisão." + ("\n" + "\n".join(errors) if errors else "")

    def rows(self):
        return [[e["id"], e["approved"], e["text"], round(e["duration"], 2)] for e in self.read()["examples"]]

    def review(self, rows):
        with _LOCK:
            data = self.read()
            known = {e["id"]: e for e in data["examples"]}
            seen = set()
            updated = []
            for identifier, approved, text, _duration in rows:
                if identifier not in known or identifier in seen:
                    raise ValueError("A lista mudou. Reabra o projeto antes de salvar a revisão.")
                seen.add(identifier)
                updated.append({**known[identifier], "approved": approved is True, "text": self.text(text)})
            if seen != set(known):
                raise ValueError("Não exclua linhas: desmarque a aprovação dos exemplos que deseja ignorar.")
            data["examples"] = updated
            self.invalidate(data)
            self.write(data)
        return self.rows()

    def fingerprint(self):
        examples = [e for e in self.read()["examples"] if e["approved"]]
        return hashlib.sha256(json.dumps(examples, sort_keys=True).encode()).hexdigest()

    def summary(self):
        data = self.read()
        approved = [e for e in data["examples"] if e["approved"]]
        duration = sum(e["duration"] for e in approved)
        ready = data.get("prepared") == self.fingerprint() and (self.path / "raw.arrow").exists()
        return (
            f"**{len(data['examples'])} exemplos · {len(approved)} aprovados · {duration / 60:.1f} min aprovados**\n\n"
            + (
                "✅ Dados preparados. Próximo passo: treinar."
                if ready
                else "Próximo passo: revisar e preparar os exemplos."
            )
        )

    def export_reviewed(self):
        approved = [e for e in self.read()["examples"] if e["approved"]]
        if len(approved) < 3 or sum(e["duration"] for e in approved) < 15:
            raise ValueError(
                "Para testar o fluxo, aprove pelo menos 3 exemplos somando 15 segundos. Para melhorar a voz, use mais gravações variadas."
            )
        content = "".join(f"{e['id']}|{e['text']}\n" for e in approved)
        (self.path / "metadata.csv").write_text(content, encoding="utf-8-sig")

    def feedback(self, checkpoint, result_audio, rating, note):
        if not result_audio or not Path(result_audio).is_file():
            raise ValueError("Gere um áudio de teste antes de avaliar.")
        if rating not in ("Ficou bom", "Precisa melhorar"):
            raise ValueError("Escolha uma avaliação.")
        with _LOCK:
            data = self.read()
            data["feedback"].append(
                {
                    "checkpoint": str(checkpoint),
                    "rating": rating,
                    "note": note,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self.write(data)
        return "Avaliação salva. Ela não inicia treinamento nem altera o modelo automaticamente."


def import_ready_audio(project, audio_files, language, consent, backend, progress=None):
    """Transcribe uploaded recordings into new, unapproved examples; never erase existing data."""
    if not consent:
        raise ValueError("Confirme que pode usar as gravações para treinamento.")
    if not audio_files:
        raise ValueError("Selecione pelo menos um arquivo de áudio.")
    added, errors = 0, []
    iterator = progress.tqdm(audio_files, desc="Importando e transcrevendo") if progress else audio_files
    for audio_path in iterator:
        try:
            # FFmpeg-backed librosa also supports input formats not handled by libsndfile.
            local_audio_input(audio_path)
            waveform, _ = backend.librosa.load(audio_path, sr=24000, mono=True)
            if len(waveform) / 24000 < 1:
                raise ValueError("áudio com menos de 1 segundo")
            chunks = (
                [(waveform, 0, len(waveform))] if len(waveform) <= 30 * 24000 else backend.Slicer(24000).slice(waveform)
            )
            for chunk, _start, _end in chunks:
                # Keep every imported example within the preparation pipeline's 30 s limit.
                for offset in range(0, len(chunk), 30 * 24000):
                    segment = chunk[offset : offset + 30 * 24000]
                    if len(segment) < 24000:
                        continue

                    with tempfile.TemporaryDirectory() as folder:
                        temporary = Path(folder) / "segment.wav"
                        sf.write(temporary, segment, 24000)
                        transcript = backend.transcribe(str(temporary), language or None)
                        project.add(temporary, transcript, True, source=Path(audio_path).name.split("-", 1)[-1])
                        added += 1
        except Exception as error:
            errors.append(f"{Path(audio_path).name}: {error}")
    message = f"{added} trechos adicionados, aguardando sua revisão e aprovação."
    if errors:
        message += "\nArquivos com problemas (os exemplos anteriores foram preservados):\n" + "\n".join(errors)
    return message


def build_guided_training(backend):
    """Build inside the existing training app, reusing its training and test engine."""

    def project(name):
        try:
            return TrainingProject(backend.path_data, name)
        except ValueError as error:
            raise gr.Error(str(error)) from error

    def editable():
        if backend.training_process is not None:
            raise gr.Error("Aguarde ou pare o treinamento antes de editar os exemplos.")

    def ui_action(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (ValueError, OSError) as error:
                raise gr.Error(str(error)) from error

        return wrapped

    def open_project(name):
        p = project(name)
        return p.name, p.rows(), p.summary(), gr.update(choices=[e[0] for e in p.rows()], value=None)

    @ui_action
    def add_example(name, audio, text, consent):
        editable()
        p = project(name)
        rows = p.add(audio, text, consent)
        return rows, p.summary(), gr.update(choices=[e[0] for e in rows], value=rows[-1][0])

    @ui_action
    def import_recordings(name, audio_files, language, consent, progress=gr.Progress()):
        editable()
        p = project(name)
        message = import_ready_audio(p, audio_files, language, consent, backend, progress)
        rows = p.rows()
        return rows, p.summary(), gr.update(choices=[e[0] for e in rows], value=rows[0][0] if rows else None), message

    @ui_action
    def review(name, rows):
        editable()
        p = project(name)
        return p.review(rows), p.summary()

    def preview(name, identifier):
        p = project(name)
        if identifier not in {e[0] for e in p.rows()}:
            return None
        return str(p.path / "wavs" / (identifier + ".wav"))

    @ui_action
    def prepare(name):
        editable()
        p = project(name)
        with _LOCK:
            data = p.read()
            p.invalidate(data)
            p.write(data)
            p.export_reviewed()
            vocabulary = Path(backend.path_data) / "Emilia_ZH_EN_pinyin" / "vocab.txt"
            if not vocabulary.exists():
                raise gr.Error("Vocabulário base ausente. Use a instalação editável do fork e mantenha a pasta data.")
            message, missing = backend.vocab_check(p.name, "pinyin")
            if missing:
                raise gr.Error(
                    "Há caracteres fora do vocabulário: "
                    + missing
                    + ". Use a aba Avançado · Vocabulário antes de continuar."
                )
            message, _ = backend.create_metadata(p.name, False)
            if not (p.path / "raw.arrow").exists() or not (p.path / "vocab.txt").exists():
                raise gr.Error(str(message))
            data = p.read()
            data["prepared"] = p.fingerprint()
            p.write(data)
        return p.summary(), "Dados preparados. Revise as opções abaixo antes de iniciar."

    def train(name, preset):
        p = project(name)
        data = p.read()
        if data.get("prepared") != p.fingerprint() or not (p.path / "raw.arrow").exists():
            raise gr.Error("Prepare os dados na etapa 3. Qualquer edição exige uma nova preparação.")
        if not torch.cuda.is_available():
            raise gr.Error(
                "O assistente de treinamento requer GPU NVIDIA/CUDA. Você pode preparar e revisar os dados nesta máquina; para treinar, use uma máquina com GPU."
            )
        if backend.training_process is not None:
            raise gr.Error("Já existe um treinamento em execução. Aguarde ou use Parar treinamento.")
        epochs = {
            "Teste rápido · 5 passagens": 5,
            "Ajuste inicial · 20 passagens": 20,
            "Ajuste prolongado · 50 passagens": 50,
        }[preset]
        yield from backend.start_training(
            p.name,
            "F5TTS_v1_Base",
            1e-5,
            800,
            "frame",
            1,
            4,
            1.0,
            epochs,
            0,
            100,
            3,
            100,
            True,
            "",
            "pinyin",
            "",
            "fp16",
            False,
            "none",
            False,
        )

    def checkpoints(name):
        return backend.get_checkpoints_project(project(name).name)

    def test(name, checkpoint, ref_audio, ref_text, text):
        p = project(name)
        allowed = backend.get_checkpoints_project(p.name, False)[0]
        if not checkpoint or checkpoint not in allowed:
            raise gr.Error("Atualize a lista e selecione um checkpoint do seu projeto.")
        if not ref_audio or not text.strip():
            raise gr.Error("Envie uma referência e escreva uma frase para testar.")
        if backend.training_process is not None:
            raise gr.Error("Pare ou aguarde o treinamento antes de testar a voz.")
        result = backend.infer(p.name, checkpoint, "F5TTS_v1_Base", ref_text, ref_audio, text, 32, True, 1.0, 42, False)
        if len(result) != 3:
            raise gr.Error(str(result[-1]))
        return (
            result[0],
            "Áudio gerado. Compare checkpoints usando a mesma referência e a mesma frase.",
            checkpoint,
            p.name,
        )

    @ui_action
    def feedback(name, tested_project, checkpoint, audio, rating, note):
        if project(name).name != tested_project:
            raise gr.Error("Este teste pertence a outro projeto. Gere um novo teste antes de avaliar.")
        return project(name).feedback(checkpoint, audio, rating, note)

    gr.Markdown("""## Treine sua voz, passo a passo
Use gravações reais com o texto correto para ajustar uma cópia do modelo.
As avaliações ajudam a organizar melhorias; **usar o aplicativo não o treina sozinho**.
O assistente usa F5-TTS v1. Comece com um teste curto e compare o resultado antes de treinar mais.
""")
    with gr.Row():
        name = gr.Textbox(
            label="1 · Nome do projeto",
            value="minha_voz",
            info="Ex.: voz_adriano. Para continuar depois, use o mesmo nome.",
        )
        open_button = gr.Button("Criar ou abrir projeto", variant="primary")
    current = gr.Textbox(label="Projeto aberto", interactive=False)
    summary = gr.Markdown("Crie ou abra um projeto para começar.")
    with gr.Accordion("2 · Enviar áudios prontos", open=True):
        gr.Markdown("""**Como funciona:** selecione seus áudios, escolha o idioma e clique em **Importar e gerar textos**.
O aplicativo transforma a fala em texto e divide gravações longas em trechos. Depois, confira tudo na etapa 3.
Você não precisa criar pastas, planilhas ou arquivos de texto. A primeira transcrição baixa o modelo e pode demorar.

**Para um bom resultado:** use gravações de uma única pessoa por projeto, sem música e com pouco ruído.
WAV, MP3 e FLAC são boas opções. M4A/OGG dependem dos codecs instalados (FFmpeg).
""")
        uploaded = gr.File(
            label="Selecione ou arraste um ou vários áudios aqui",
            file_count="multiple",
            type="filepath",
            file_types=[".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"],
        )
        language = gr.Dropdown(
            choices=[("Português", "pt"), ("Inglês", "en"), ("Espanhol", "es"), ("Detectar automaticamente", "")],
            value="pt",
            label="Em que idioma a pessoa está falando?",
        )
        consent = gr.Checkbox(label="Tenho autorização para usar estas gravações no treinamento", value=False)
        import_button = gr.Button("Importar e gerar textos", variant="primary")
        import_status = gr.Textbox(label="O que está acontecendo com seus arquivos", lines=4, interactive=False)
        with gr.Accordion("Já tenho o texto ou quero gravar um exemplo pelo microfone", open=False):
            gr.Markdown(
                "Adicione um trecho de 1 a 30 segundos com a transcrição pronta. Prefira trechos de 3 a 12 segundos."
            )
            audio = gr.Audio(
                label="Grave pelo microfone ou envie um arquivo", sources=["upload", "microphone"], type="filepath"
            )
            text = gr.Textbox(
                label="O que foi dito no áudio?", lines=3, placeholder="Transcreva exatamente todas as palavras."
            )
            add_button = gr.Button("Adicionar exemplo com este texto")
    with gr.Accordion("3 · Ouvir, corrigir e aprovar", open=True):
        gr.Markdown(
            "Ouça cada exemplo, corrija o texto na tabela e marque Aprovado. A coluna Código identifica a gravação; não a altere. Exemplos não aprovados ficam fora do treinamento."
        )
        sample = gr.Dropdown(label="Escolha uma gravação para ouvir", choices=[])
        sample_audio = gr.Audio(label="Conferir gravação", type="filepath", interactive=False)
        table = gr.Dataframe(
            headers=["Código", "Aprovado", "Texto correto", "Segundos"],
            datatype=["str", "bool", "str", "number"],
            type="array",
            interactive=True,
        )
        with gr.Row():
            review_button = gr.Button("Salvar revisão")
            prepare_button = gr.Button("Preparar exemplos aprovados", variant="primary")
        gr.Markdown(
            "Primeiro salve a revisão; depois prepare. O mínimo de 3 exemplos / 15 s serve apenas para testar o fluxo. Uma voz melhor requer mais gravações limpas, variadas e revisadas; não há garantia de melhoria."
        )
    with gr.Accordion("4 · Treinar", open=True):
        gr.Markdown(
            "Requer GPU NVIDIA/CUDA e espaço em disco. O primeiro treino baixa o modelo base. O original é preservado e as versões ficam na pasta ckpts do projeto. Se faltar memória, pare e use os controles avançados na aba Avançado · Treinar."
        )
        preset = gr.Radio(
            ["Teste rápido · 5 passagens", "Ajuste inicial · 20 passagens", "Ajuste prolongado · 50 passagens"],
            value="Teste rápido · 5 passagens",
            label="Quanto treinar?",
            info="Mais passagens não garantem melhor qualidade.",
        )
        with gr.Row():
            start = gr.Button("Iniciar treinamento", variant="primary")
            stop = gr.Button("Parar treinamento", interactive=False)
        status = gr.Textbox(label="Andamento e mensagens", lines=5, interactive=False)
    with gr.Accordion("5 · Testar e avaliar", open=True):
        refresh = gr.Button("Buscar versões treinadas")
        checkpoint = gr.Dropdown(label="Versão do modelo (checkpoint)", choices=[])
        reference = gr.Audio(label="Áudio de referência curto", type="filepath")
        reference_text = gr.Textbox(label="Texto da referência (opcional; vazio usa transcrição automática)")
        test_text = gr.Textbox(label="Frase nova para testar", value="Olá! Este é um teste da minha voz.")
        generate = gr.Button("Ouvir esta versão", variant="primary")
        tested_checkpoint = gr.State("")
        tested_project = gr.State("")
        result = gr.Audio(label="Resultado do teste", type="filepath", interactive=False)
        test_status = gr.Textbox(label="Resultado da avaliação", interactive=False)
        rating = gr.Radio(["Ficou bom", "Precisa melhorar"], label="Como ficou?")
        note = gr.Textbox(
            label="O que precisa melhorar?", placeholder="Ex.: pulou uma palavra, pronunciou errado, ficou com ruído…"
        )
        save_feedback = gr.Button("Guardar avaliação")
        gr.Markdown(
            "Avaliações ficam salvas no projeto. Para ensinar uma correção, adicione uma gravação correta na etapa 2, aprove e prepare os dados novamente. Cada novo treino gera/continua os checkpoints do projeto; não há aprendizado automático a cada clique."
        )
    open_button.click(open_project, [name], [current, table, summary, sample])
    import_button.click(
        import_recordings, [current, uploaded, language, consent], [table, summary, sample, import_status]
    )
    add_button.click(add_example, [current, audio, text, consent], [table, summary, sample])
    sample.change(preview, [current, sample], [sample_audio])
    review_button.click(review, [current, table], [table, summary])
    prepare_button.click(prepare, [current], [summary, status])
    start.click(train, [current, preset], [status, start, stop], concurrency_limit=1, concurrency_id="guided-training")
    stop.click(backend.stop_training, outputs=[status, start, stop], queue=False)
    refresh.click(checkpoints, [current], [checkpoint])
    generate.click(
        test,
        [current, checkpoint, reference, reference_text, test_text],
        [result, test_status, tested_checkpoint, tested_project],
    )
    save_feedback.click(feedback, [current, tested_project, tested_checkpoint, result, rating, note], [test_status])
