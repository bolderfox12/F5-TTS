# Changelog

## 1.3.2 — 2026-11-15

### Added

- Audio library search and category filters, waveform previews, non-destructive WAV trimming, and a shortcut to save clips as voice references.
- Localized library controls in all seven interface languages.

## 1.3.1 — 2026-11-15

### Added

- Rebuilt voice training into data review, GPU tuning, checkpoint comparison, and inference publishing areas.
- Upload a compatible starting checkpoint and vocabulary; persist architecture, precision, frame batch,
  gradient accumulation/clipping, data-loader workers, learning rate, warmup, epochs, and checkpoint retention.
- Live GPU dashboard with VRAM use and optional `nvidia-smi` utilization, temperature, and power readings.
- Compare multiple retained checkpoints using one saved reference and the same test phrase; rate samples and
  optionally save an approved generated sample as a reusable voice reference.
- Register a selected training checkpoint in My models without copying its weights.
- My models inventory reports disk use, protects models referenced by story/narration/training projects,
  and can remove managed checkpoint files after confirmation.
- Files page to copy, move, download, and delete generated audio, saved references, and subtitle files.
  Moving updates History, voice, transcript, and narration paths; external copies remain manageable locally.

### Updated

- English README documents the new training, model storage, and file library workflows.
- F5 fine-tuning CLI accepts a configurable number of CPU data-loader workers.

## 1.3.0 — 2026-11-15

### Added

- Persistent interface language selection: English, Brazilian Portuguese, Spanish, French,
  German, Simplified Chinese, and Japanese, using bundled offline message catalogs.
- Localized page navigation, forms, status/error messages, microphone controls, training steps,
  and built-in examples, while preserving user text, names, voice IDs, and model IDs.
- Stories and characters: cast management, one voice and model per character, and automatic
  parsing of `Character: dialogue` scripts, including full-width colons for Chinese/Japanese.
- Dialogue, YouTube, and short-drama templates; per-turn editing, pauses, ordering, and removal.
- Complete story WAV, speaker-labeled SRT, and editable character-script TXT downloads.
- Reuse of unchanged dialogue audio and checkpoint/vocabulary-aware model invalidation.
- Browser integration checks for seven languages, three voice references, story editing,
  downloads, and preservation of saved stories when changing the interface language.

### Updated

- Focus the English README on the current Studio web interface, removing obsolete Gradio,
  specialized runtime, legacy CLI, and unrelated deployment instructions.
- Keep existing paragraph narration projects and workspaces compatible.
- Include translation catalogs and story modules in release-package checks.

## 1.2.0 — 2026-11-15

### Added

- Browser microphone recording for voice references in Create audio, My voices, and checkpoint comparisons.
- Recording timer, automatic 30-second limit, preview playback, explicit selection, and microphone cleanup.
- FFmpeg conversion of browser recordings to managed mono 24 kHz WAV references.
- A persistent My models library for local checkpoints and training vocabularies, with file uploads or local paths.
- Model selection in Create audio and Revoice, while retaining the original English/Chinese model.
- F5-TTS v1 Base, F5-TTS Base, and E2-TTS Base architecture choices for registered checkpoints.
- Windows model-path handling under WSL and model names in generated-audio history.
- Reproducible browser and real-model GPU smoke-test scripts.
- English documentation, release notes, and GitHub release packaging with wheel/source archives.

### Fixed

- Scale generated signals that exceed PCM limits before WAV export to avoid clipping.
- Allow slow HTTP-test startup on Windows-mounted WSL drives and include subprocess startup logs on failures.
- Remove inherited pytest screen-test detection from the standalone NiceGUI test server.
- Include the microphone JavaScript component in installed distributions.

### Validation

- 36 tests and 14 subtests passed, including all ten Studio pages and real FFmpeg conversions.
- Chromium with a simulated microphone passed recording, preview, track cleanup, voice saving,
  simulated permission denial, model registration, and switching back to the original model.
- Original F5-TTS v1 Base/Vocos synthesis ran on an RTX 3080 Ti under WSL2 with Python 3.12.16:
  6.848-second English and 8-second Portuguese WAVs at 24 kHz, finite and non-silent, without saturation.
- Portuguese pronunciation quality, private trained checkpoints, full GPU training, and Whisper/NLLB
  quality with downloaded weights remain outside this validation.

This release includes the earlier local narration, subtitles, pronunciation, audio-tools,
guided-training, and NiceGUI work from the Studio branch. No model weights are bundled.
