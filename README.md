# F5 Studio

[![Python](https://img.shields.io/badge/Python-3.11%E2%80%933.15-brightgreen)](pyproject.toml)
[![Release](https://img.shields.io/github/v/release/bolderfox12/F5-TTS)](https://github.com/bolderfox12/F5-TTS/releases/latest)
[![License: MIT](https://img.shields.io/badge/Code-MIT-blue.svg)](LICENSE)

A local web studio for creating speech, saved voices, narrated stories, subtitles, and voice models.
F5 Studio uses [F5-TTS](https://github.com/SWivid/F5-TTS) for speech synthesis and runs processing on your computer.

**Version 1.3.5** adds a multilingual training workspace with BigVGAN, single- and multi-GPU CUDA
training, live resource charts and progress, RAM caching, and INT8 CPU export.
See the [complete guide in Brazilian Portuguese](docs/guia-studio.md), [release notes](docs/releases/v1.3.5.md),
and [changelog](CHANGELOG.md).

## What you can do

| Page | Purpose |
| --- | --- |
| Create audio | Record or upload a reference, select a voice and model, then export WAV or MP3 with quality, denoising, and stereo options. Track inference progress and live CPU, GPU/VRAM, RAM, and disk usage. |
| My voices | Keep named reference recordings and transcripts for reuse. |
| My models | Inspect model files and disk usage, register checkpoints, export INT8 copies, and remove unused managed weights. |
| Audio library | Find and filter generated audio and voice references; preview waveforms, trim audio into a new WAV, save clips as voices, copy, move, download, or delete files. |
| Stories and characters | Build a cast, assign each character a voice and model, write dialogue, and export the complete story. |
| Narration projects | Work with paragraph-based narration, voice assignments, segment edits, pauses, and WAV/SRT export. |
| Transcription and subtitles | Transcribe locally, correct segments, translate subtitles, and export TXT/SRT. |
| Revoice | Create a new narration from revised or translated text with the chosen voice and model. |
| Audio tools | Convert WAV/MP3, adjust volume, reduce steady noise, and trim silence with FFmpeg, with in-page explanations for each effect. |
| Train a voice | Import and review multilingual examples, train with one or more CUDA GPUs, monitor progress and resource usage, compare checkpoints, and publish or quantize models. |
| History | Replay and download generated audio, with its model and generation settings. |
| Settings | Choose CPU/CUDA, speed, generation steps, pronunciation rules, and cache preferences. |

The application listens on **http://127.0.0.1:7862** and is intended for one local user.
No paid API keys are needed for these features. Model weights require an initial download.

## Install and open Studio

### Requirements

- Git and FFmpeg/ffprobe available on `PATH`.
- Python **3.11–3.15**, as declared by the package. **Python 3.12** is the locally validated setup.
- On Linux, `libsndfile`; for NVIDIA acceleration, a driver compatible with the PyTorch CUDA build.

Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install ffmpeg libsndfile1
```

On Windows, install FFmpeg and add its `bin` folder to `PATH`. On macOS with Homebrew,
use `brew install ffmpeg libsndfile`.

For Chinese or Japanese in a Linux browser, install CJK fonts if needed:
`sudo apt-get install fonts-noto-cjk`. Windows/macOS browsers use their system font fallback.

### Create an isolated environment

```bash
git clone https://github.com/bolderfox12/F5-TTS.git
cd F5-TTS
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
```

Linux/macOS:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

### Install the application

Install PyTorch for your hardware using the [official installation selector](https://pytorch.org/get-started/locally/).
For CPU-only use:

```bash
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
```

Then install this checkout and open the web interface:

```bash
python -m pip install -e ".[studio]"
python -m pip check
f5-tts_studio
```

Open **http://127.0.0.1:7862**. If the Windows environment is not activated, use
`.\.venv\Scripts\python.exe` and `.\.venv\Scripts\f5-tts_studio.exe`.

Startup options:

```bash
f5-tts_studio --no-browser
f5-tts_studio --port 7870
f5-tts_studio --workspace /path/to/your/workspace
```

Install from this fork's checkout or its release wheel to get the Studio changes.
The ordinary PyPI package name alone does not select this fork.

## Choose your interface language

Use the language selector in the page header. Available languages include English, Brazilian
Portuguese, Spanish, French, German, Simplified Chinese, and Japanese.

The preference is saved in the workspace and applied when the page reloads. Menus, forms,
notifications, the recorder, training controls, and built-in examples use local translations;
no translation service or additional language model is required.

Story text, character names, saved voice names, and model IDs remain as you entered them.
The language of generated speech depends on the chosen checkpoint and your script.

## Record a voice reference

In **Create audio**, **My voices**, or the training checkpoint comparison:

1. Click **Record with microphone** and allow the browser to use your microphone.
2. Speak, then click **Stop recording**. The automatic limit is 30 seconds; prefer up to 12 seconds.
3. Listen to the preview and click **Use this recording**.
4. Enter its transcript if known, then generate audio or save the voice.

FFmpeg converts the selected recording to mono 24 kHz WAV. The microphone is released when
recording stops or you leave the page. The recording is sent to your local Studio only after
you select it. Uploading an existing audio file remains available.

Use `localhost` or `127.0.0.1` for microphone permission. Use clean speech without music.

## Create a story with characters and different voices

Open **Stories and characters**:

1. Give the story a name.
2. Build the cast. Each character has a name, a saved reference voice, and a model.
3. Type or paste the script using `Character: dialogue`. You can start with a dialogue,
   YouTube, or short-drama example.
4. Click **Save story and assign dialogue**.
5. Select a turn to change its text, character, position, or pause, or generate just that turn.
6. Click **Generate complete story**, then download **WAV**, **SRT**, and **TXT**.

Example with the English interface and the default cast:

```text
Narrator: It was a quiet morning until someone knocked at the door.

Ana: Did you hear that noise too?

Bruno: I did. Let's find out together.

Narrator: And that is how the adventure began.
```

Use the exact names in your cast; names are matched without case sensitivity. Following lines
continue the current character's turn. The first cast member narrates initial text without a speaker label.
The initial narrator name and examples follow the interface language; your saved names are kept unchanged.
Unknown speaker labels are reported before the project is saved.

Assign distinct saved references to make characters sound different. Creating a character name does not
invent a new voice. You can select your Portuguese fine-tune for one character and another registered
model for another character.

Existing, unchanged dialogue audio is reused. Editing one turn regenerates only that turn;
reordering or renaming characters can reuse finished audio. Changing a character's voice or model
requires its affected turns to be generated again. Pause duration is configurable per turn.
You can stop after the current turn and continue later.

The final WAV joins the turns and pauses. SRT subtitles include the character name and segment timings;
TXT contains the editable character script. These files can be used in your video editor for YouTube
or other storytelling projects. Video editing, background music, and lip synchronization are not part
of this audio workflow.

## Register and switch voice models

In **My models**, name your model, select its `.pt`, `.pth`, `.ckpt`, or `.safetensors` checkpoint,
and provide the training `vocab.txt`. Choose the architecture used for training: F5-TTS v1 Base,
F5-TTS Base, F5-TTS Base · BigVGAN, or E2-TTS Base. Leave the vocabulary blank only if training
retained the original vocabulary.

You can upload the files or paste their paths. Windows paths such as `C:\Models\portuguese.pt`
are supported under WSL. Uploaded models are stored in the workspace; pasted paths keep referring
to the original files, which must stay accessible.

Select a registered model in **Create audio**, **Revoice**, or each story character's model field.
The original English/Chinese F5-TTS option stays available. Switching does not replace other checkpoints.

The model library shows each registered checkpoint's size and whether it is used by a story or
narration. Models in use are protected from removal. For an uploaded checkpoint managed by the
Studio, choose **Remove** and confirm whether to delete the weights from disk. Files registered
from outside the Studio can be removed from the library without deleting the original file.

## Manage generated files

Open **Files** to review generated WAV files, saved voice references, unused uploads, subtitles, and
transcripts. Enter a destination path and choose whether to copy or move each file. A move updates
the matching History, voice, narration, or transcript paths so the file remains available to the
Studio. A copy leaves the source in place. Windows paths such as `C:\Users\YourName\Music\F5 Studio`
are accepted when the Studio runs under WSL. Existing destination files are never overwritten.

Deleting an audio file removes related History entries and invalidates narration audio that used
it. A voice reference cannot be deleted while an existing story or narration uses that voice.

## Train and compare voice checkpoints

Open **Train a voice** to create or resume a project. Use an editable checkout for advanced
training and BigVGAN so the project can use the base vocabulary and the NVIDIA submodule.
A separate [Brazilian Portuguese guide](docs/guia-studio.md#treinamento-de-voz) covers the same workflow.

1. **Data and transcripts.** Upload multiple recordings. Studio can transcribe and segment recordings
   with Whisper, or read a pasted manifest with one `filename.wav|transcript` per short file.
   Review each clip, check its RMS level and clipping, and approve it for training. Enable vocabulary
   expansion to add tokens from approved transcripts while retaining the original token IDs.
2. **Training and GPU.** Select the architecture and an optional compatible starting checkpoint and
   vocabulary. The initial download for a public base checkpoint is around 1.4 GB. Use the suggested
   GPU profile or configure BF16/FP16, frame budget, accumulation, clipping, workers, warmup,
   8-bit Adam, activation recomputation (F5-TTS), TF32 (Ampere and newer), and checkpoint retention.
   The optional validation percentage reserves approved clips before training. Each epoch reports
   EMA loss on up to 24 of the reserved clips. Use different recordings and review their transcripts
   before reserving a validation split.
   Use **Multiple CUDA GPUs** to distribute training over all visible GPUs. Leave it off for one
   GPU. Resume with the same number of GPUs. The optional **RAM spectrogram cache** reuses computed
   features and divides the configured GiB budget across processes. Actual cache allocation is
   capped by available host memory. The resource monitor charts CPU, RAM, disk occupancy and
   throughput, GPU utilization, and VRAM. Training progress reports completed and planned updates.
3. **Evaluate checkpoints.** Select checkpoints, a reference clip, and a fixed phrase to generate
   comparable samples. Try phrases that cover the target language's sounds and writing system.
4. **Use for inference.** Register a checkpoint and its training vocabulary in **My models**.
5. **Quantization.** Export a checkpoint's linear layers as INT8 for CPU inference. You can also create
   an INT8 copy from **My models**. The Studio reports both file sizes. To transfer a custom model,
   download its INT8 weights and vocabulary from the library and register them on the other machine
   with the same architecture. This format needs an x86 PyTorch quantized backend and preserves the
   vocoder in its original precision.

For a transcript manifest, enter one line per uploaded clip using its original filename.

```text
clip-01.wav|This is the first recording from the session.
clip-02.wav|The second recording has a different intonation.
```

Use a fresh project to change base architecture, adjust the vocabulary after training, or fine-tune
a prior checkpoint with different data. Resuming an existing run uses its saved optimizer and data
order. The default model stays selectable alongside project checkpoints.

For **F5-TTS Base · BigVGAN**, initialize the NVIDIA submodule once:

```bash
git submodule update --init -- src/third_party/BigVGAN
```

This option uses the compatible SWivid acoustic checkpoint and NVIDIA BigVGAN v2 at 24 kHz with
100 mel bands. The vocoder is downloaded on first synthesis or when generating training previews.
Studio fine-tunes the acoustic model and uses BigVGAN's pretrained generator for waveform synthesis.

## Subtitles, audio tools, and training

- **Transcription:** local Whisper Small or Turbo, with editable text segments and TXT/SRT export.
- **Subtitle translation:** local NLLB for Portuguese, English, Spanish, French, and German.
  Review the translation; the original transcript is preserved.
- **Revoice:** synthesize revised or translated text with a saved reference and a selected model.
  This creates a new performance rather than preserving the original timing or delivery.
- **Audio tools:** FFmpeg conversion, volume balancing, steady-noise reduction, and silence trimming.
  The basic noise filter does not separate voice and music.
- **Train a voice:** import recordings in the web interface, review and explicitly approve examples,
  prepare data, start fine-tuning, and compare saved checkpoints. NVIDIA/CUDA is required by the guided
  training presets; data preparation can run on CPU.

Training examples must be 1–30 seconds; prefer 3–12 seconds. Three examples totaling 15 seconds
are only a workflow minimum, not enough-data advice for a high-quality voice. Edits require preparation
again. Ratings do not update weights: add a corrected recording and train again to teach a correction.

## Workspaces and model downloads

The default workspace is `~/.f5tts-studio`. Use `--workspace` to keep voices, models, stories,
uploads, history, and preferences in another directory. Keep using the same path to retain access
to your existing content. Training data and checkpoints in an editable checkout use `data/` and `ckpts/`.

The optional `bash iniciar-studio.sh` launcher uses `.studio/` and `.cache/huggingface` inside this checkout.

The first F5-TTS v1 Base + Vocos download is about **1.40 GB**, plus small configuration files.
PyTorch/CUDA dependencies need additional space. Whisper and NLLB are separate downloads.
Supplying the reference transcript avoids an ASR download solely for that reference.

To work entirely offline, download all required weights first, then set:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 f5-tts_studio
```

PowerShell:

```powershell
$env:HF_HUB_OFFLINE="1"
$env:TRANSFORMERS_OFFLINE="1"
.\.venv\Scripts\f5-tts_studio.exe
```

## Troubleshooting

| Symptom | Check |
| --- | --- |
| CUDA is unavailable | Confirm that `nvidia-smi` sees the GPU and that `torch.cuda.is_available()` returns `True` in the Studio environment. |
| Too few batches for multi-GPU training | Try a smaller frame budget, more approved recordings, or a single GPU. |
| GPU memory runs out | Reduce frames or examples per batch, then consider mixed precision, gradient accumulation, and F5-TTS activation recomputation. |
| Host memory pressure | Lower the RAM cache limit or turn off the cache. With caching off, reduce data-loader workers if needed. |
| Checkpoint and vocabulary do not match | Select the architecture and the vocabulary used by the checkpoint. Reprepare the approved dataset after changing the base vocabulary. |
| BigVGAN is unavailable | Initialize the NVIDIA submodule from an editable checkout. |
| An INT8 copy fails to load | Check for an x86 quantized PyTorch backend and use the original checkpoint architecture and vocabulary. |
| Port 7862 is in use | Start with `f5-tts_studio --port 7870` and pass the workspace you want to open. |

## Validation and development

```bash
python -m pip install pytest
python -m pytest tests -q -p no:cacheprovider
```

Version 1.3.5 validation included **49 automated tests**, browser checks for batch import, review,
training controls, live resource charts, and INT8 download. Synthetic training verified distributed
execution with two CPU processes, gradient accumulation, validation, and checkpointing.
BigVGAN inference produced 24 kHz WAV audio on an RTX 3080 Ti. INT8 CPU inference reduced the
public F5-TTS v1 Base checkpoint from about **1.35 GB to 355 MB** in that test. The machine had
one physical CUDA GPU, so the tests did not measure multi-GPU CUDA performance.
The HTTP startup timeout is configurable through `F5_STUDIO_TEST_STARTUP_TIMEOUT` (default: 120 seconds).

For the opt-in browser test with a temporary workspace and simulated synthesis:

```bash
python -m pip install playwright
python -m playwright install --with-deps chromium --only-shell
python testar-historias-navegador.py
```

It checks all seven interface languages, story creation with three voice references, dialogue editing,
WAV/SRT/TXT downloads, and reopening the same story after changing the interface language.

`testar-microfone-navegador.py` checks the recorder against a separate test instance as described in its
source. `testar-modelo-real.py` is an opt-in NVIDIA GPU synthesis check; it downloads about 1.40 GB if
weights are missing and saves samples in the checkout's `.studio` workspace.

The original-model GPU smoke test used Python 3.12.16, PyTorch 2.15.0+cu132, TorchAudio 2.11.1+cu132,
and an RTX 3080 Ti under WSL2. It produced finite, non-silent 24 kHz English and Portuguese WAVs without
PCM saturation. This does not establish Portuguese pronunciation quality or validate a private checkpoint.
Complete fine-tuning and Whisper/NLLB quality with downloaded weights were not part of that check.

Before committing:

```bash
python -m pip install pre-commit
pre-commit run --all-files
```

## Upstream

This Studio builds on **F5-TTS: A Fairytaler that Fakes Fluent and Faithful Speech with Flow Matching**
and retains the upstream code license and acknowledgements below.

- [Upstream repository](https://github.com/SWivid/F5-TTS)
- [Research paper](https://arxiv.org/abs/2410.06885)
- [Original demos](https://swivid.github.io/F5-TTS/)
- [Model repository](https://huggingface.co/SWivid/F5-TTS)


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
- [Yuekai Zhang](https://github.com/yuekaizhang) upstream runtime contributions ~

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
