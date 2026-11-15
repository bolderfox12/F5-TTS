# F5-TTS: A Fairytaler that Fakes Fluent and Faithful Speech with Flow Matching

[![python](https://img.shields.io/badge/Python-3.15.2-brightgreen)](https://github.com/bolderfox12/F5-TTS/tree/python/latest-stable)
[![arXiv](https://img.shields.io/badge/arXiv-2410.06885-b31b1b.svg?logo=arXiv)](https://arxiv.org/abs/2410.06885)
[![demo](https://img.shields.io/badge/GitHub-Demo-orange.svg)](https://swivid.github.io/F5-TTS/)
[![hfspace](https://img.shields.io/badge/🤗-HF%20Space-yellow)](https://huggingface.co/spaces/mrfakename/E2-F5-TTS)
[![msspace](https://img.shields.io/badge/🤖-MS%20Space-blue)](https://modelscope.cn/studios/AI-ModelScope/E2-F5-TTS)
[![lab](https://img.shields.io/badge/🏫-X--LANCE-grey?labelColor=lightgrey)](https://x-lance.sjtu.edu.cn/)
[![lab](https://img.shields.io/badge/🏫-SII-grey?labelColor=lightgrey)](https://www.sii.edu.cn/)
[![lab](https://img.shields.io/badge/🏫-PCL-grey?labelColor=lightgrey)](https://www.pcl.ac.cn)
<!-- <img src="https://github.com/user-attachments/assets/12d7749c-071a-427c-81bf-b87b91def670" alt="Watermark" style="width: 40px; height: auto"> -->

**F5-TTS**: Diffusion Transformer with ConvNeXt V2, faster trained and inference.

**E2 TTS**: Flat-UNet Transformer, closest reproduction from [paper](https://arxiv.org/abs/2406.18009).

**Sway Sampling**: Inference-time flow step sampling strategy, greatly improves performance

### Thanks to all the contributors !

## News
- **2025/03/12**: 🔥 F5-TTS v1 base model with better training and inference performance. [Few demo](https://swivid.github.io/F5-TTS_updates).
- **2024/10/08**: F5-TTS & E2 TTS base models on [🤗 Hugging Face](https://huggingface.co/SWivid/F5-TTS), [🤖 Model Scope](https://www.modelscope.cn/models/SWivid/F5-TTS_Emilia-ZH-EN), [🟣 Wisemodel](https://wisemodel.cn/models/SJTU_X-LANCE/F5-TTS_Emilia-ZH-EN).


## Alterações deste fork

- **Python 3.15.2:** instalação e dependências atualizadas; leitura e gravação de áudio sem TorchCodec obrigatório. Disponível na branch [`python/latest-stable`](https://github.com/bolderfox12/F5-TTS/tree/python/latest-stable), no [PR #2](https://github.com/bolderfox12/F5-TTS/pull/2).
- **Nova interface:** estúdio de voz com controles principais em português, visual verde-petróleo e fluxo em três etapas. Disponível na branch [`interface/estudio-voz-pt-br`](https://github.com/bolderfox12/F5-TTS/tree/interface/estudio-voz-pt-br), no [PR #1](https://github.com/bolderfox12/F5-TTS/pull/1).
- **Verificações:** testes de compatibilidade com Python 3.15.2 em CPU e verificações de código aprovados no GitHub. Geração com checkpoints reais, treinamento completo e GPU ainda precisam de validação.

As duas melhorias estão em revisão e ainda não foram integradas à `main`.
Siga a seção de instalação para usar a branch de Python 3.15.2.

## Instalação — Python 3.15.2

> **Importante:** os ajustes de compatibilidade estão na branch
> [`python/latest-stable`](https://github.com/bolderfox12/F5-TTS/tree/python/latest-stable)
> ([PR #2](https://github.com/bolderfox12/F5-TTS/pull/2)). Faça o `git switch`
> indicado abaixo antes de instalar. A branch `main` ainda não recebeu esses ajustes.

Este fork foi atualizado para **CPython 3.15.2**, a versão estável mais recente
verificada em 15/11/2026 ([Python.org](https://www.python.org/downloads/release/python-3152/)).
Use a distribuição normal com GIL; builds free-threaded não foram validados.
A instalação, importação, leitura/gravação de WAV e execução forward/backward de
um modelo pequeno são testadas em CPU. Isso não equivale a validar treinamento
completo, checkpoints, qualidade de voz ou todos os aceleradores.

### 1. Pré-requisitos

- Git, Python 3.15.2 e FFmpeg/ffprobe disponíveis no terminal.
- NVIDIA: driver compatível com a versão CUDA do PyTorch escolhido.
- Windows: instalar o FFmpeg e adicionar sua pasta `bin` ao PATH.
- Ubuntu/Debian: `sudo apt-get install ffmpeg libsndfile1`.
- macOS com Homebrew: `brew install ffmpeg libsndfile`.

Confira: `ffmpeg -version`, `ffprobe -version` e `git --version`.

### 2. Baixar este fork e criar um ambiente isolado

```bash
git clone https://github.com/bolderfox12/F5-TTS.git
cd F5-TTS
# Enquanto esta atualização estiver em revisão:
git switch python/latest-stable
```

**Windows (PowerShell):**

```powershell
py -3.15 -m venv .venv
# A ativação é opcional: estes comandos usam diretamente o Python do ambiente.
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
```

**Linux/macOS:**

```bash
python3.15 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

Nos comandos abaixo, no Windows substitua `python` por
`.\.venv\Scripts\python.exe` se não ativou o ambiente.

### 3. Instalar PyTorch antes do projeto

**CPU (rota usada nos testes locais):**

```bash
python -m pip install "torch>=2.15.0" torchaudio --index-url https://download.pytorch.org/whl/cpu
```

**NVIDIA, AMD, Intel ou Apple Silicon:** use o seletor oficial
[PyTorch](https://pytorch.org/get-started/locally/) para escolher um build estável
que ofereça wheels para Python 3.15 e suporte à sua GPU e ao seu driver.
Não reutilize os comandos antigos para torch 2.8/CUDA 12.8: eles não são a rota
para Python 3.15. Não misture arbitrariamente versões de torch e torchaudio;
consulte a [matriz do TorchAudio](https://docs.pytorch.org/audio/stable/installation.html).
A combinação validada em CPU neste fork é torch 2.15.0 + torchaudio 2.11.1.

### 4. Instalar e verificar

```bash
python -m pip install -e .
python -m pip check
python -m unittest discover -s tests -v
```

A instalação editável usa **este fork**. `pip install f5-tts` instala a distribuição
publicada no PyPI e não inclui automaticamente estas modificações.
Para o vocoder BigVGAN opcional: `git submodule update --init --recursive`.
A leitura/gravação de áudio do aplicativo usa SoundFile, sem exigir TorchCodec.
A compatibilidade do pydub com Python 3.13+ é fornecida por `audioop-lts`.
WAV é o formato recomendado; outros codecs dependem do libsndfile/FFmpeg disponível.

### 5. Abrir a interface

```bash
f5-tts_infer-gradio --inbrowser
```

No Windows sem ativação:

```powershell
.\.venv\Scripts\f5-tts_infer-gradio.exe --inbrowser
```

O primeiro uso baixa os modelos e precisa de internet e espaço em disco.
Os checkpoints originais são voltados a inglês e chinês; traduzir a interface
não adiciona idiomas ao modelo.

### Docker (CPU, Python 3.15.2)

```bash
docker build -f Dockerfile.python315 -t f5tts-python315 .
docker run --rm -p 7860:7860 -v f5-tts-cache:/root/.cache/huggingface/hub f5tts-python315
```

O Dockerfile.python315 usa os arquivos deste checkout. A imagem publicada
`ghcr.io/swivid/f5-tts:main` pertence ao projeto original e não contém
necessariamente esta atualização. O Dockerfile original com CUDA foi preservado. O Dockerfile.python315 é voltado a CPU;
para GPU, prepare uma imagem compatível com seu runtime NVIDIA/ROCm.
O runtime especializado Triton/TensorRT-LLM em `src/f5_tts/runtime/` mantém seu
ambiente próprio e não foi migrado nem validado com Python 3.15.
Os extras de avaliação (`.[eval]`) também precisam de validação independente.

### Runtime

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
