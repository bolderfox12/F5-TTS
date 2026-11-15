# F5 Studio — Local Voice Production with F5-TTS

[![Python](https://img.shields.io/badge/Python-3.11%E2%80%933.15-brightgreen)](pyproject.toml)
[![Release](https://img.shields.io/github/v/release/bolderfox12/F5-TTS)](https://github.com/bolderfox12/F5-TTS/releases/latest)
[![License: MIT](https://img.shields.io/badge/Code-MIT-blue.svg)](LICENSE)
[![arXiv](https://img.shields.io/badge/arXiv-2410.06885-b31b1b.svg?logo=arXiv)](https://arxiv.org/abs/2410.06885)

F5 Studio is a local voice production interface built on [F5-TTS](https://github.com/SWivid/F5-TTS).
Record a voice reference, generate speech, switch between your own checkpoints and the original model,
prepare narration projects, and work with subtitles and audio files on your computer.

This fork includes a NiceGUI Studio, guided fine-tuning, local transcription and translation,
and the original advanced Gradio, CLI, and training tools. The Studio interface currently uses
Brazilian Portuguese labels; this README and the release notes are in English.

**Current release: F5 Studio 1.2.0.** See the [release notes](docs/releases/v1.2.0.md) and [changelog](CHANGELOG.md).

## Features

| Area | What you can do |
| --- | --- |
| Create audio — **Criar áudio** | Record or upload a reference, select a saved voice and model, generate speech, preview it, and download WAV. |
| My voices — **Minhas vozes** | Save named recordings and their transcripts. Saving a reference does not train a model. |
| My models — **Meus modelos** | Register local checkpoints and vocabularies, including a Portuguese fine-tune, while keeping the original English/Chinese model available. |
| Narration projects — **Projetos de narração** | Edit and reorder paragraphs, assign voices and pauses, generate changed segments, and export WAV and SRT. |
| Transcription and subtitles — **Transcrição e legendas** | Run Whisper Small or Turbo locally, review segments, and export TXT/SRT. Translate subtitles with local NLLB. |
| Revoice — **Regravar áudio** | Generate a new narration from revised or translated text using a saved voice and your selected model. |
| Audio tools — **Ferramentas de áudio** | Convert WAV/MP3, adjust volume, reduce steady noise, and trim silence with FFmpeg. |
| Train a voice — **Treinar uma voz** | Import recordings, review and approve examples, prepare a dataset, fine-tune, and compare checkpoints. |
| History — **Histórico** | Replay and download generated audio, including the model name and generation settings. |
| Settings — **Configurações** | Choose CPU/CUDA, speed, generation steps, pronunciation rules, and download preferences. |

Speech and text processing run locally. Model weights require an initial download; no paid API keys
are needed for these Studio features. The app binds to `127.0.0.1` and is intended for one local user.

## Installation

### 1. Requirements

- Git and FFmpeg/ffprobe on your `PATH`.
- Python within the package range **3.11–3.15**. **Python 3.12 is the locally validated setup for this release.**
- For NVIDIA acceleration, a driver compatible with your selected PyTorch CUDA build.
- On Linux, the system `libsndfile` audio library.

Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install ffmpeg libsndfile1
```

On Windows, install FFmpeg and add its `bin` directory to `PATH`. On macOS, use
`brew install ffmpeg libsndfile` if you use Homebrew.

Check the tools before continuing:

```bash
git --version
ffmpeg -version
ffprobe -version
```

### 2. Clone this fork and create an isolated environment

```bash
git clone https://github.com/bolderfox12/F5-TTS.git
cd F5-TTS
```

The release is available on `main`; the Studio development branch is `studio/local-tools`.
For the exact release source, use `git checkout v1.2.0` after cloning.

**Windows PowerShell:**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
```

If you do not activate the environment, replace `python` with `.\.venv\Scripts\python.exe`
and run Studio using `.\.venv\Scripts\f5-tts_studio.exe`.

**Linux/macOS:**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

### 3. Install PyTorch for your hardware

For NVIDIA, AMD, Intel, or Apple Silicon, follow the official
[PyTorch installation selector](https://pytorch.org/get-started/locally/) for your operating system,
Python version, and accelerator. Check the [TorchAudio compatibility guidance](https://docs.pytorch.org/audio/stable/installation.html).

For a CPU-only environment:

```bash
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
```

The local GPU smoke test used **Python 3.12.16, PyTorch 2.15.0+cu132, TorchAudio 2.11.1+cu132,
and an NVIDIA RTX 3080 Ti with 12 GB of VRAM under WSL2**. This is a tested configuration,
not a requirement to install those exact versions on every computer.

### 4. Install and open Studio

```bash
python -m pip install -e ".[studio]"
python -m pip check
f5-tts_studio
```

Open **http://127.0.0.1:7862**. You can also use:

```bash
f5-tts_studio --no-browser
f5-tts_studio --port 7870
f5-tts_studio --workspace /path/to/your/workspace
```

Installing `f5-tts` from PyPI alone does not select this fork. Install from this checkout or
use the distribution files attached to this fork's GitHub release, with the `studio` extra.
Optional BigVGAN support also needs `git submodule update --init --recursive`.
Audio I/O uses SoundFile; TorchCodec is not required by this fork's audio helpers.

## Record a reference with your microphone

In **Criar áudio**, **Minhas vozes**, or the training checkpoint comparison:

1. Click **Gravar pelo microfone** and allow microphone access in your browser.
2. Speak clearly, then click **Parar gravação**. Recording stops automatically at 30 seconds; prefer up to 12 seconds.
3. Listen to the preview and click **Usar esta gravação**.
4. Generate speech or save the voice. Enter the reference transcript if you already know it.

The microphone is released when recording stops or you leave the page. Audio is sent to the local
Studio only after you click **Usar esta gravação**, then FFmpeg converts it to mono 24 kHz WAV.
Uploading an existing recording remains available.

Use `http://127.0.0.1:7862` or `http://localhost:7862` for browser microphone access.
An externally hosted browser origin would require HTTPS; the supplied Studio server binds only to loopback.

## Keep your Portuguese model and the original model

1. Open **Meus modelos** and enter a name such as **My Portuguese model**.
2. Choose your `.pt`, `.pth`, `.ckpt`, or `.safetensors` checkpoint and the training `vocab.txt`,
   or paste their local file paths. Windows paths such as `C:\Models\portuguese.pt` are accepted under WSL.
3. Select the architecture used for training: **F5-TTS v1 Base**, **F5-TTS Base**, or **E2-TTS Base**.
4. Click **Cadastrar modelo**.
5. In **Criar áudio**, select it under **Modelo para gerar áudio**. In **Regravar áudio**, use **Modelo para regravar**.

The **F5-TTS original · inglês / chinês** option stays available. Switching models does not overwrite
other checkpoints, and History records which model generated each audio file. Weights are loaded only
when synthesis needs them. Models trained inside Studio remain available through the trained-project controls.

Use the vocabulary from training; leave it blank only if the training kept the original vocabulary.
Uploaded model files are stored in the workspace. Pasted paths refer to the original files, which must
remain accessible. Registering a name does not train or convert a model, and a saved voice is a reference,
not a checkpoint. Portuguese pronunciation depends on the checkpoint and its training data.

## Narration, subtitles, and local audio tools

For narration, separate paragraphs with blank lines, assign voices, then save edits before generating.
Changed text, voice references, pronunciation rules, and trained checkpoints invalidate previous segment
audio. **Parar depois do trecho atual** stops after the current segment while preserving finished work.
Exported SRT timings describe segments rather than word-level alignment.

Transcription supports `openai/whisper-small` and `openai/whisper-large-v3-turbo`.
Subtitle translation uses `facebook/nllb-200-distilled-600M` for Portuguese, English, Spanish, French, and German.
The original transcription is preserved; translated subtitles keep the source segment timestamps.
Review recognition and translation results before exporting or generating a new narration.

FFmpeg provides format conversion, volume adjustment, steady-noise reduction, and silence trimming.
The noise filter does not separate voices from music. Text-based revoicing does not preserve the original
performance, emotion, timing, or lip synchronization.

## Storage, downloads, and offline operation

By default, Studio stores voices, uploads, generated files, settings, and model registrations in
`~/.f5tts-studio`. Use `--workspace` to select another directory. In an editable checkout,
training projects use `data/` and checkpoints use `ckpts/`.

The optional Linux launcher `bash iniciar-studio.sh` uses `.studio/` for the workspace and
`.cache/huggingface` for model downloads inside this checkout. Keep using the same launch method
or workspace path to access the same voices and history.

The initial **F5-TTS v1 Base + Vocos** download is approximately **1.40 GB**:
1,348,435,761 bytes for the checkpoint and 54,365,991 bytes for the vocoder, plus small configuration files.
PyTorch/CUDA dependencies require additional disk space. Whisper and NLLB are separate downloads;
providing your reference transcript avoids downloading an ASR model solely to transcribe that reference.

The cache preference in Settings applies to the local transcription and translation tools.
For completely offline use, download every model you need first, including synthesis and vocoder weights:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 f5-tts_studio
```

On Windows PowerShell:

```powershell
$env:HF_HUB_OFFLINE="1"
$env:TRANSFORMERS_OFFLINE="1"
.\.venv\Scripts\f5-tts_studio.exe
```

Missing weights must be downloaded before enabling offline mode. Model licenses still apply;
see [License](#license) and the individual model repositories.

## Guided fine-tuning

Open **Treinar uma voz**, or use the advanced Gradio training interface:

```bash
f5-tts_finetune-gradio --port 7861
```

The guided workflow is:

1. Create or open a project.
2. Import recordings, select the language, and generate transcripts. Long recordings are split into examples.
3. Listen to each example, correct its transcript, explicitly approve it, and prepare the dataset.
4. Select a training profile, start fine-tuning, and monitor or stop the process.
5. Compare saved checkpoints with a reference and a new sentence, and save your evaluation.

Examples are stored as mono 24 kHz WAV and must be 1–30 seconds long; prefer 3–12 seconds.
Three examples totaling 15 seconds are only the minimum for trying the workflow, not enough-data guidance
for a good voice. Editing examples invalidates the prepared dataset. Saved ratings do not update weights;
to teach a correction, add a correct recording and prepare/train again.

The guided presets target NVIDIA/CUDA fine-tuning of F5-TTS v1. Existing project checkpoints can be resumed;
create another project for an independent experiment. Heavy operations within one Studio instance are
serialized. Full GPU training was not part of this release's local validation.

## Validation and development

Install the test runner and run the offline suite:

```bash
python -m pip install pytest
python -m pytest tests -q -p no:cacheprovider
```

Release validation: **36 tests and 14 subtests passed**, covering persistence, model registration/switching,
microphone conversion, dataset review, narration invalidation, FFmpeg conversions, and all ten Studio pages.
The HTTP startup timeout is configurable through `F5_STUDIO_TEST_STARTUP_TIMEOUT` (default: 120 seconds),
which helps with slow imports on Windows-mounted WSL drives.

Browser validation on Linux with a simulated microphone:

```bash
python -m pip install playwright
python -m playwright install --with-deps chromium --only-shell
# Start this in a separate terminal:
f5-tts_studio --port 7863 --no-browser --workspace /tmp/f5-studio-microphone-test
# Then run:
python testar-microfone-navegador.py
```

The browser check records audio, previews it, releases the microphone, saves a reference/voice, simulates
permission denial, registers a dummy checkpoint without loading it, and switches back to the original model.
It uses the temporary workspace, not your usual Studio workspace.

For an opt-in real-model GPU smoke test, run `python testar-modelo-real.py`. It downloads approximately
1.40 GB of weights if missing, uses the bundled English reference, and adds English and Portuguese samples
to the checkout's `.studio` history. The locally validated outputs were 6.848 and 8 seconds at 24 kHz,
with finite, non-silent samples and no PCM saturation after the export fix.

The local validation does not establish Portuguese pronunciation quality, validate your private trained
checkpoint, run complete fine-tuning, or validate Whisper/NLLB quality with downloaded weights.
TensorRT is not integrated into Studio; it uses the PyTorch backend. The specialized runtime below
has its own setup. Evaluation extras and other hardware configurations need separate validation.

## Upstream project

This fork builds on **F5-TTS: A Fairytaler that Fakes Fluent and Faithful Speech with Flow Matching**,
including the Diffusion Transformer/ConvNeXt V2 architecture and Sway Sampling.
It also retains the E2 TTS Flat-UNet Transformer reproduction and the original advanced tools.

- [Upstream repository](https://github.com/SWivid/F5-TTS)
- [Paper](https://arxiv.org/abs/2410.06885)
- [Original demos](https://swivid.github.io/F5-TTS/)
- [Hugging Face models](https://huggingface.co/SWivid/F5-TTS)
- [Advanced inference guidance](src/f5_tts/infer/README.md)
- [Training guidance](src/f5_tts/train/README.md)

## Specialized runtime


Deployment solution with Triton and TensorRT-LLM.

#### Benchmark Results
Decoding on a single L20 GPU, using 26 different prompt_audio & target_text pairs, 16 NFE.

| Model               | Concurrency    | Avg Latency | RTF    | Mode            |
|---------------------|----------------|-------------|--------|-----------------|
| F5-TTS Base (Vocos) | 2              | 253 ms      | 0.0394 | Client-Server   |
| F5-TTS Base (Vocos) | 1 (Batch_size) | -           | 0.0402 | Offline TRT-LLM |
| F5-TTS Base (Vocos) | 1 (Batch_size) | -           | 0.1467 | Offline Pytorch |

See [detailed instructions](src/f5_tts/runtime/triton_trtllm/README.md) for more information.


## Inference

- In order to achieve desired performance, take a moment to read [detailed guidance](src/f5_tts/infer).
- By properly searching the keywords of problem encountered, [issues](https://github.com/SWivid/F5-TTS/issues?q=is%3Aissue) are very helpful.

### 1. Gradio App

Currently supported features:

- Basic TTS with Chunk Inference
- Multi-Style / Multi-Speaker Generation
- Voice Chat powered by Qwen2.5-3B-Instruct
- [Custom inference with more language support](src/f5_tts/infer/SHARED.md)

```bash
# Launch a Gradio app (web interface)
f5-tts_infer-gradio

# Specify the port/host
f5-tts_infer-gradio --port 7860 --host 0.0.0.0

# Launch a share link
f5-tts_infer-gradio --share
```

<details>
<summary>NVIDIA device docker compose file example</summary>

```yaml
services:
  f5-tts:
    image: ghcr.io/swivid/f5-tts:main
    ports:
      - "7860:7860"
    environment:
      GRADIO_SERVER_PORT: 7860
    entrypoint: ["f5-tts_infer-gradio", "--port", "7860", "--host", "0.0.0.0"]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

volumes:
  f5-tts:
    driver: local
```

</details>

### 2. CLI Inference

```bash
# Run with flags
# Leave --ref_text "" will have ASR model transcribe (extra GPU memory usage)
f5-tts_infer-cli --model F5TTS_v1_Base \
--ref_audio "provide_prompt_wav_path_here.wav" \
--ref_text "The content, subtitle or transcription of reference audio." \
--gen_text "Some text you want TTS model generate for you."

# Run with default setting. src/f5_tts/infer/examples/basic/basic.toml
f5-tts_infer-cli
# Or with your own .toml file
f5-tts_infer-cli -c custom.toml

# Multi voice. See src/f5_tts/infer/README.md
f5-tts_infer-cli -c src/f5_tts/infer/examples/multi/story.toml
```


## Training

### 1. With Hugging Face Accelerate

Refer to [training & finetuning guidance](src/f5_tts/train) for best practice.

### 2. With Gradio App

```bash
# Quick start with Gradio web interface
f5-tts_finetune-gradio
```

Read [training & finetuning guidance](src/f5_tts/train) for more instructions.


## [Evaluation](src/f5_tts/eval)


## Development

Use pre-commit to ensure code quality (will run linters and formatters automatically):

```bash
pip install pre-commit
pre-commit install
```

When making a pull request, before each commit, run: 

```bash
pre-commit run --all-files
```

Note: Some model components have linting exceptions for E722 to accommodate tensor notation.


## Acknowledgements

- [E2-TTS](https://arxiv.org/abs/2406.18009) brilliant work, simple and effective
- [Emilia](https://arxiv.org/abs/2407.05361), [WenetSpeech4TTS](https://arxiv.org/abs/2406.05763), [LibriTTS](https://arxiv.org/abs/1904.02882), [LJSpeech](https://keithito.com/LJ-Speech-Dataset/) valuable datasets
- [lucidrains](https://github.com/lucidrains) initial CFM structure with also [bfs18](https://github.com/bfs18) for discussion
- [SD3](https://arxiv.org/abs/2403.03206) & [Hugging Face diffusers](https://github.com/huggingface/diffusers) DiT and MMDiT code structure
- [torchdiffeq](https://github.com/rtqichen/torchdiffeq) as ODE solver, [Vocos](https://huggingface.co/charactr/vocos-mel-24khz) and [BigVGAN](https://github.com/NVIDIA/BigVGAN) as vocoder
- [FunASR](https://github.com/modelscope/FunASR), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [UniSpeech](https://github.com/microsoft/UniSpeech), [SpeechMOS](https://github.com/tarepan/SpeechMOS) for evaluation tools
- [ctc-forced-aligner](https://github.com/MahmoudAshraf97/ctc-forced-aligner) for speech edit test
- [mrfakename](https://x.com/realmrfakename) huggingface space demo ~
- [f5-tts-mlx](https://github.com/lucasnewman/f5-tts-mlx/tree/main) Implementation with MLX framework by [Lucas Newman](https://github.com/lucasnewman)
- [F5-TTS-ONNX](https://github.com/DakeQQ/F5-TTS-ONNX) ONNX Runtime version by [DakeQQ](https://github.com/DakeQQ)
- [Yuekai Zhang](https://github.com/yuekaizhang) Triton and TensorRT-LLM support ~

## Citation
If our work and codebase is useful for you, please cite as:
```
@article{chen-etal-2024-f5tts,
      title={F5-TTS: A Fairytaler that Fakes Fluent and Faithful Speech with Flow Matching}, 
      author={Yushen Chen and Zhikang Niu and Ziyang Ma and Keqi Deng and Chunhui Wang and Jian Zhao and Kai Yu and Xie Chen},
      journal={arXiv preprint arXiv:2410.06885},
      year={2024},
}
```
## License

Our code is released under MIT License. The pre-trained models are licensed under the CC-BY-NC license due to the training data Emilia, which is an in-the-wild dataset. Sorry for any inconvenience this may cause.
