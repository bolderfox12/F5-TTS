# F5-TTS: A Fairytaler that Fakes Fluent and Faithful Speech with Flow Matching

[![python](https://img.shields.io/badge/Python-3.10-brightgreen)](https://github.com/SWivid/F5-TTS)
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

## F5 Studio local — narração, legendas e ferramentas de áudio

A branch `studio/local-tools` amplia o estúdio para uso no próprio PC, sem chaves
ou chamadas a APIs pagas. Inclui as atualizações das branches anteriores.
Os modelos são baixados na primeira utilização e o processamento de voz/texto
acontece localmente. O acesso às ferramentas continua em `http://127.0.0.1:7862`.

Com o ambiente Python/PyTorch preparado:

```bash
git fetch origin
git switch studio/local-tools
python -m pip install -e ".[studio]"
f5-tts_studio
```

No Windows sem ativar o ambiente: use `.\.venv\Scripts\python.exe -m pip install -e ".[studio]"`
e depois `.\.venv\Scripts\f5-tts_studio.exe`.

### Ferramentas locais disponíveis

| Área | Fluxo |
| --- | --- |
| Projetos de narração | Cole o roteiro, separado por linhas em branco; escolha vozes por trecho, edite, reordene, defina pausas, gere somente o que mudou e junte em WAV + SRT. |
| Modelos no roteiro | Aplique opcionalmente um checkpoint do seu projeto treinado. Mudanças no checkpoint, texto, voz, velocidade ou pronúncia invalidam o áudio anterior para nova exportação. |
| Transcrição e legendas | Envie áudio, selecione Whisper Small ou Turbo, revise cada trecho e exporte TXT/SRT. |
| Tradução de legendas | Use NLLB local para português, inglês, espanhol, francês ou alemão. A versão original é preservada e a tradução mantém os tempos originais. |
| Regravar áudio | Carregue texto revisado/traduzido, escolha uma voz e opcionalmente um checkpoint e gere outra narração. Não preserva exatamente atuação, emoção, duração ou sincronização do original. |
| Ferramentas de áudio | FFmpeg local para converter WAV/MP3, equilibrar volume, reduzir ruído contínuo e cortar silêncios. A redução simples de ruído não separa voz e música com IA. |
| Pronúncia | Em Configurações, informe uma regra por linha, como `IA = inteligência artificial`. A leitura é substituída na geração; o roteiro original permanece salvo. |

No editor, clique em um trecho para editar e **salve antes de gerar**. O botão
**Parar depois do trecho atual** mantém os trechos prontos. Ao continuar, são gerados
somente os que estiverem pendentes. A exportação inclui pausas entre os trechos;
as legendas indicam intervalos de trechos, sem alinhamento palavra por palavra.
Projetos, transcrições, traduções, avaliações e preferências persistem no workspace.

### Modelos, downloads e funcionamento sem internet

- F5-TTS continua responsável pela síntese, referência de voz e ajuste fino.
- `openai/whisper-small`: transcrição local mais leve.
- `openai/whisper-large-v3-turbo`: alternativa mais pesada, útil para comparação.
- `facebook/nllb-200-distilled-600M`: tradução local. O primeiro uso baixa pesos e tokenizador.
- FFmpeg precisa estar instalado e disponível no PATH para as ferramentas de áudio.

As opções de download em Configurações podem exigir somente arquivos já no cache
para as duas novas ferramentas de transcrição e tradução. Para execução totalmente
sem rede, baixe previamente todos os modelos que pretende usar, inclusive os de
síntese/vocoder e os do treinamento. Depois, antes de iniciar o programa:

```bash
# Linux/macOS
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 f5-tts_studio
```

```powershell
# Windows PowerShell
$env:HF_HUB_OFFLINE="1"
$env:TRANSFORMERS_OFFLINE="1"
.\.venv\Scripts\f5-tts_studio.exe
```

Modelos ausentes no cache precisam ser baixados antes de usar esse modo.
Não há cobrança de API neste programa. As licenças de cada modelo continuam
aplicáveis: os pesos originais F5-TTS e o NLLB têm restrições de uso comercial.
Referências: [Whisper Turbo](https://huggingface.co/openai/whisper-large-v3-turbo),
[NLLB-200](https://huggingface.co/facebook/nllb-200-distilled-600M).

### Escopo e validação

Este é um estúdio local com identidade própria. Ainda não oferece conversão de voz
que preserve a atuação, dublagem de vídeo sincronizada/lip-sync, criação de vozes
por descrição, música/efeitos gerados por IA ou agentes de voz conversacionais.
Esses recursos exigem modelos e integrações adicionais. A qualidade do português
continua dependendo do checkpoint F5-TTS e dos dados usados no ajuste fino.

Testes locais usam modelos simulados para orquestração de ASR, tradução e síntese;
a conversão WAV/MP3 é executada com FFmpeg real. São verificados revisão,
preservação dos originais, exportação SRT, múltiplas vozes, invalidação de trechos,
parada/retomada e respostas HTTP das nove páginas. Não foi executado download de
Whisper/NLLB, validação de qualidade com pesos reais ou treinamento completo em GPU.
A conferência visual no navegador continua pendente.

## Novo F5 Studio — interface NiceGUI

A branch `studio/nicegui` reúne criação de áudio, vozes salvas, treinamento guiado,
histórico e configurações em uma interface local em português, com menu lateral.
Ela inclui as alterações de Python 3.15.2 e treinamento das branches anteriores.
O Gradio continua disponível como alternativa avançada.

Depois de criar o ambiente Python e instalar o PyTorch conforme a seção abaixo:

```bash
git fetch origin
git switch studio/nicegui
python -m pip install -e ".[studio]"
f5-tts_studio
```

Abra `http://127.0.0.1:7862`. No Windows sem ativar o ambiente, use
`.\.venv\Scripts\python.exe -m pip install -e ".[studio]"` e
`.\.venv\Scripts\f5-tts_studio.exe`.
Para outra porta: `f5-tts_studio --port 7870`. Para não abrir automaticamente o
navegador: `f5-tts_studio --no-browser`.

### O que cada área faz

| Área | Como usar |
| --- | --- |
| Criar áudio | Escolha uma voz salva ou envie referência curta, selecione opcionalmente um checkpoint treinado, escreva o roteiro, gere, ouça e baixe WAV. |
| Minhas vozes | Salve referências e transcrições com nomes fáceis de encontrar. Salvar uma referência não treina o modelo. |
| Treinar uma voz | Crie/abra projeto, envie vários áudios, gere textos, revise cada trecho, aprove, prepare, treine e teste checkpoints. |
| Histórico | Reproduza e baixe os áudios gerados; os testes guardam projeto, checkpoint e avaliações. |
| Configurações | Veja a GPU detectada e escolha Automático/CPU/CUDA, velocidade e 16/32/48 etapas de geração. |

Os modelos só são carregados na primeira operação que precisa deles. O primeiro
uso exige internet para baixar pesos. Vozes, referências e histórico ficam por
padrão em `~/.f5tts-studio`; use `--workspace /caminho/para/pasta` para outra pasta.
Os projetos de treinamento continuam em `data` e os checkpoints em `ckpts` do
checkout editável, compatíveis com o treinamento anterior.
O aplicativo é local, para uma pessoa por vez, e escuta apenas em `127.0.0.1`.
Não inclui autenticação nem implantação pública. Operações pesadas são
serializadas para evitar treinamento e geração disputando a GPU dentro do estúdio.
Não execute simultaneamente treinamento em outro aplicativo/terminal na mesma GPU.

### NVIDIA: qualidade e velocidade

CUDA permite usar a GPU para geração e ajuste fino. Uma RTX 3090 pode aproveitar
a rota CUDA, desde que o driver e o build PyTorch sejam compatíveis.
TensorRT é voltado à otimização de execução; **não torna a voz melhor automaticamente**.
Precisão reduzida pode mudar os resultados e precisa de comparação:
[NVIDIA — accuracy considerations](https://docs.nvidia.com/deeplearning/tensorrt/latest/inference-library/accuracy-considerations.html).
Este estúdio usa o backend PyTorch existente; não instala nem ativa TensorRT.
O runtime Triton/TensorRT-LLM original continua separado e exige validação própria.

Para buscar naturalidade, priorize gravações limpas, transcrições corretas e
ajuste fino revisado. O perfil com mais etapas é uma opção de comparação, sem
promessa de qualidade superior. Os modelos originais são voltados a inglês e
chinês; suporte de qualidade em português depende dos checkpoints e dos dados.

Validação do estúdio: testes de persistência, geração com modelo simulado,
configurações, exclusão mútua e resposta HTTP das cinco páginas em Python 3.15.2.
Sem validação visual no navegador, geração real com checkpoints ou treinamento
completo em GPU neste ambiente. O treinamento e importação reaproveitam o fluxo
anterior, com testes offline de preparação e revisão.

## Installation / Instalação (Python 3.15.2)

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

### Treinamento guiado — novo assistente

Na branch `training/guided` (baseada na atualização Python 3.15.2):

```bash
git switch training/guided
python -m pip install -e .
f5-tts_finetune-gradio --port 7861
```

No Windows sem ativar o ambiente, use `.\.venv\Scripts\f5-tts_finetune-gradio.exe --port 7861`.
Abra `http://127.0.0.1:7861` e escolha **Comece aqui · Treinamento guiado**:

1. **Projeto:** crie um nome ou abra um projeto já salvo.
2. **Áudios prontos:** arraste um ou vários arquivos, escolha o idioma e clique em Importar e gerar textos. A transcrição é automática e os áudios longos são divididos. Também há uma opção para gravação pelo microfone ou transcrição manual.
3. **Revisão:** ouça, corrija os textos, marque os exemplos aprovados, salve e prepare.
4. **Treinamento:** escolha um perfil, inicie e acompanhe; o botão Parar interrompe o processo.
5. **Teste:** busque checkpoints, gere uma frase e registre se ficou bom ou o que precisa melhorar.

Os exemplos são convertidos para WAV mono / 24 kHz e ficam na pasta de dados do projeto.
Cada trecho precisa ter 1–30 s; prefira 3–12 s. Três exemplos totalizando 15 s são apenas o
mínimo para experimentar o fluxo, não uma recomendação de dados suficientes para uma boa voz.
Os exemplos precisam ser aprovados explicitamente. Edições invalidam os dados preparados.
Avaliações ficam salvas em `reviewed_examples.json`; elas não atualizam os pesos automaticamente.
Para ensinar uma correção, adicione uma gravação correta e prepare/treine novamente.

O assistente é voltado ao ajuste fino de F5-TTS v1 com GPU NVIDIA/CUDA e configurações simples
(sem logger externo e sem otimizador de 8 bits). O primeiro treino baixa o checkpoint base.
Os arquivos são mantidos em `data/<projeto>_pinyin` e `ckpts/<projeto>`; para projetos anteriores,
use as abas avançadas. Os presets retomam checkpoints do mesmo projeto quando disponíveis;
para um experimento independente, crie outro projeto.
Não há promessa de melhoria: compare checkpoints com a mesma referência e uma frase nova.
Os controles técnicos de vocabulário, transcrição e treinamento continuam nas abas avançadas.

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
