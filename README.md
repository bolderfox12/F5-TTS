# F5 Studio

[![Python](https://img.shields.io/badge/Python-3.11%E2%80%933.15-brightgreen)](pyproject.toml)
[![Release](https://img.shields.io/github/v/release/bolderfox12/F5-TTS)](https://github.com/bolderfox12/F5-TTS/releases/latest)
[![License: MIT](https://img.shields.io/badge/Code-MIT-blue.svg)](LICENSE)

A local web studio for creating speech, saved voices, narrated stories, subtitles, and voice models.
F5 Studio uses [F5-TTS](https://github.com/SWivid/F5-TTS) for speech synthesis and runs processing on your computer.

**Version 1.3.3:** audio export controls for WAV/MP3, quality, optional denoising and stereo, plus a refreshed animated navigation and in-context audio tool guides. See the [release notes](docs/releases/v1.3.3.md)
and [changelog](CHANGELOG.md).

## What you can do

| Page | Purpose |
| --- | --- |
| Create audio | Record or upload a reference, select a voice and model, then export WAV or MP3 with quality, denoising, and stereo options. |
| My voices | Keep named reference recordings and transcripts for reuse. |
| My models | Inspect model files and disk usage, register checkpoints, and remove managed weights when they are no longer needed. |
| Audio library | Find and filter generated audio and voice references; preview waveforms, trim audio into a new WAV, save clips as voices, copy, move, download, or delete files. |
| Stories and characters | Build a cast, assign each character a voice and model, write dialogue, and export the complete story. |
| Narration projects | Work with paragraph-based narration, voice assignments, segment edits, pauses, and WAV/SRT export. |
| Transcription and subtitles | Transcribe locally, correct segments, translate subtitles, and export TXT/SRT. |
| Revoice | Create a new narration from revised or translated text with the chosen voice and model. |
| Audio tools | Convert WAV/MP3, adjust volume, reduce steady noise, and trim silence with FFmpeg, with in-page explanations for each effect. |
| Train a voice | Review recordings, tune GPU training, upload a starting checkpoint, compare saved versions, and publish a selected checkpoint for inference. |
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

Use the language selector in the page header, labeled **Idioma da interface** in Portuguese
and **Interface language** in English. Available languages:

- English
- Português (Brasil)
- Español
- Français
- Deutsch
- 简体中文 — Simplified Chinese
- 日本語 — Japanese

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
F5-TTS Base, or E2-TTS Base. Leave the vocabulary blank only if training retained the original vocabulary.

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

Open **Train a voice** and create a project or continue an existing one. The page is organized into
four areas: **Data and transcripts**, **Training and GPU**, **Evaluate checkpoints**, and **Use for
inference**.

1. Import recordings from one speaker, review the transcripts, and approve only examples that are
   clean and correctly transcribed. Training uses the approved real recordings; generated samples
   are not automatically added to the training set.
2. Optionally upload a compatible F5 checkpoint and its vocabulary to start from those weights.
   If no starting checkpoint is provided, the first run uses the F5 base weights and may download
   about 1.4 GB if they are not already cached.
3. The GPU panel reports VRAM, current memory use, and—when `nvidia-smi` is available—GPU load,
   temperature, and power. **Optimize for this GPU** sets a conservative frame batch, mixed
   precision, and an 8-bit optimizer profile. Advanced controls include batch frames, examples per
   batch, gradient accumulation and clipping, CPU data-loader workers, learning rate, epochs,
   warmup, and checkpoint retention. Reduce the frame batch if training runs out of VRAM.
4. Refresh the checkpoint list and select one or several versions. The comparison uses the same
   voice reference and text for each selected checkpoint. Listen and rate each result; a good result
   can be saved as a reusable voice reference.
5. In **Use for inference**, register the selected checkpoint in **My models**. The checkpoint and
   its matching vocabulary stay in their training locations; the app adds a library entry without
   copying the weights.

Training settings and starting weights are locked to a project's first training run. Create another
project to experiment with a different architecture or base checkpoint. The Studio does not switch
the active inference model automatically after training.
A saved voice is a reference recording; a model contains the trained weights.

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

## Validation and development

```bash
python -m pip install pytest
python -m pytest tests -q -p no:cacheprovider
```

Validation: **47 tests passed**, plus browser checks in all seven languages using a temporary
workspace and simulated synthesis. The suite covers character parsing, separate references and models, cache invalidation and reuse,
WAV/SRT output, workspace compatibility, all interface catalogs and placeholders, and Studio pages.
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
