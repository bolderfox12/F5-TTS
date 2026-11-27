# F5 Studio

[![Python](https://img.shields.io/badge/Python-3.11%E2%80%933.15-brightgreen)](pyproject.toml)
[![Licença MIT](https://img.shields.io/badge/C%C3%B3digo-MIT-blue)](LICENSE)

Studio local para **criar áudio, dublar vídeos com IA ou com voz humana e editar a montagem**.
A interface usa NiceGUI e abre no navegador. A branch `main` contém os recursos descritos aqui.
O número de versão declarado pelo pacote continua sendo **1.3.6**. [Histórico de versões](CHANGELOG.md)

- [Instalação](#instalação)
- [Dublagem por IA e gravação humana](#dublagem-por-ia-e-gravação-humana)
- [Editor de vídeo avançado](#editor-de-vídeo-avançado)
- [Criação de áudio, modelos e treinamento](#criação-de-áudio-modelos-e-treinamento)
- [Workspace, atualização e diagnóstico](#workspace-atualização-e-diagnóstico)

## Instalação

### Requisitos

- **Git**, **FFmpeg** e **FFprobe** no `PATH`. Para incorporar legendas ou texto, use um FFmpeg com `subtitles` e `drawtext`. O filtro `rubberband`, quando disponível, melhora as alterações extremas de pitch.
- **Python 3.15** para o launcher deste repositório. O pacote declara suporte de Python 3.11 a 3.15. A configuração de referência atual do Studio foi usada com **Python 3.15.2 no Linux/WSL2**.
- **uv** para seguir os comandos abaixo. [Instalação do uv](https://docs.astral.sh/uv/getting-started/installation/)
- **Voz por IA na dublagem** usa o ambiente separado do Chatterbox. O Whisper também usa esse ambiente para identificar personagens. **Música e efeitos separados do original** usam o Demucs opcional. A gravação humana funciona localmente no navegador.

No Ubuntu, Debian ou WSL2:

```bash
sudo apt update
sudo apt install git ffmpeg libsndfile1 build-essential
```

No Windows, instale Git e adicione a pasta `bin` do FFmpeg ao `PATH`. No macOS, as bibliotecas de
sistema podem ser instaladas com `brew install ffmpeg libsndfile`. Em Linux, instale
`fonts-noto-cjk` se faltarem caracteres em coreano, japonês ou chinês na interface ou nas legendas.

### Studio no Linux ou WSL2

O exemplo instala o backend CPU. Para NVIDIA, substitua `--torch-backend=cpu` por um backend
CUDA correspondente ao driver, como `--torch-backend=cu132` quando compatível. Mantenha
PyTorch e TorchAudio no mesmo ambiente e no mesmo backend. [Guia do PyTorch no uv](https://docs.astral.sh/uv/guides/integration/pytorch/)

```bash
git clone https://github.com/bolderfox12/F5-TTS.git
cd F5-TTS
uv venv --python 3.15 .studio/interface-python315
uv pip install --python .studio/interface-python315/bin/python --torch-backend=cpu -e ".[studio]"
uv pip check --python .studio/interface-python315/bin/python
bash iniciar-studio.sh
```

Abra [http://127.0.0.1:7862](http://127.0.0.1:7862). Use
`bash iniciar-studio.sh --port 7872 --no-browser` para iniciar na porta 7872 e abrir
[http://127.0.0.1:7872](http://127.0.0.1:7872) manualmente.

### Equivalente no Windows PowerShell

Execute no clone do repositório, com as dependências de sistema instaladas:

```powershell
uv venv --python 3.15 .studio/interface-python315
$studioPython = ".studio/interface-python315/Scripts/python.exe"
uv pip install --python $studioPython --torch-backend=cpu -e ".[studio]"
uv pip check --python $studioPython
& $studioPython -m f5_tts.studio.app --workspace .studio
```

Em outro ambiente com o extra `studio` instalado, também é possível iniciar com
`f5-tts_studio --workspace .studio`. Esse comando aceita `--port` e `--no-browser`.

### Chatterbox Multilingual V3

Crie o segundo ambiente dentro do workspace. O exemplo usa o commit do Chatterbox validado neste
Studio. Para CUDA, escolha um backend compatível nos dois ambientes. [Código do Chatterbox validado](https://github.com/resemble-ai/chatterbox/commit/ab55923c27757c90cfcc407baa57dc25a7d25217)

```bash
uv venv --python 3.15 .studio/chatterbox/.venv
uv pip install --python .studio/chatterbox/.venv/bin/python --torch-backend=cpu \
  "torch>=2.15.0" "torchaudio>=2.10.0" \
  "chatterbox-tts @ git+https://github.com/resemble-ai/chatterbox.git@ab55923c27757c90cfcc407baa57dc25a7d25217"
uv pip check --python .studio/chatterbox/.venv/bin/python
.studio/chatterbox/.venv/bin/python -c "from chatterbox.mtl_tts import ChatterboxMultilingualTTS"
```

No Windows, use `.studio/chatterbox/.venv/Scripts/python.exe` nos comandos após `uv venv`.
Com outro workspace, use a estrutura `<workspace>/chatterbox/.venv`.

### Fish Speech S2-Pro na dublagem

O seletor **Fish Speech S2-Pro** usa um servidor Fish Speech separado em
`http://127.0.0.1:8080`. Instale o Fish Speech fora deste repositório, com Python 3.12,
seguindo o [guia oficial de instalação](https://speech.fish.audio/install/) e baixe os pesos
`fishaudio/s2-pro`. No diretório do Fish Speech, inicie o servidor com:

```bash
uv sync --python 3.12 --extra cu129
hf download fishaudio/s2-pro --local-dir checkpoints/s2-pro
.venv/bin/python tools/api_server.py --listen 127.0.0.1:8080 \
  --llama-checkpoint-path checkpoints/s2-pro \
  --decoder-checkpoint-path checkpoints/s2-pro/codec.pth \
  --decoder-config-name modded_dac_vq
```

Se o servidor usar outro endereço, configure `F5_FISH_SPEECH_URL` antes de iniciar o Studio.
Se o servidor foi iniciado com autenticação `--api-key`, configure também
`F5_FISH_SPEECH_API_KEY` no ambiente do Studio; não salve essa chave em arquivos do repositório.
O Fish precisa do texto transcrito da referência de voz. O S2-Pro pede pelo menos 24 GB de
memória de GPU; a memória de duas placas não é somada automaticamente. Leia também a
[licença do Fish Speech](https://github.com/fishaudio/fish-speech/blob/main/LICENSE), que
tem termos diferentes da licença MIT deste Studio.

### Demucs para música e efeitos

O ambiente abaixo foi validado em CPU. O Studio procura o executável em
`<workspace>/demucs/.venv`, no `PATH` ou no caminho informado em `F5_STUDIO_DEMUCS`.
Os pesos `htdemucs` são baixados no primeiro uso. [Projeto Demucs](https://github.com/facebookresearch/demucs)

```bash
uv venv --python 3.12 .studio/demucs/.venv
uv pip install --python .studio/demucs/.venv/bin/python --torch-backend=cpu \
  "torch==2.8.0" "torchaudio==2.8.0" "demucs==4.0.1" soundfile
uv pip check --python .studio/demucs/.venv/bin/python
```

No Windows, use `.studio/demucs/.venv/Scripts/python.exe`. Para exportar só com as vozes,
deixe **Áudio de fundo** em 0%.

## Dublagem por IA e gravação humana

Use **Dublagem → Dublagem por IA** ou **Dublagem convencional com humanos**. O vídeo,
o roteiro e os takes são compartilhados ao alternar de modo dentro da sessão aberta.

- **Entrada e transcrição.** Importe MP4, MOV, MKV ou WebM de até **500 MB**. Escolha
  Whisper Small, Whisper Turbo ou Gemini. A segunda passada em intervalos com som pode recuperar
  falas ausentes. Os cartões identificam os trechos recuperados e permitem corrigir texto, tempos
  e personagem. Ouça e escolha a referência de voz de cada personagem.
- **Tradução e síntese.** Escolha NLLB local ou Gemini, independentemente do transcritor.
  A dublagem oferece português, inglês, espanhol, francês, alemão, coreano, japonês e mandarim
  em caracteres simplificados. O Chatterbox, Fish Speech S2-Pro ou um modelo registrado gera as falas. Ouça, edite
  e regenere cada trecho. A compatibilidade de idioma depende do modelo escolhido.
- **Ajuste de duração.** Os cartões medem quanto a fala excede a janela. O ajuste de velocidade
  da IA vai até **1,30×**. O Gemini pode sugerir expressões mais curtas por fala ou testá-las
  automaticamente na prévia. O Studio mede o áudio regerado antes de aplicar uma alternativa.
  A sincronização usa os tempos da fala na timeline.
- **Gravação humana (ADR).** Escolha o microfone, confira o medidor, grave com bipes e contagem
  3–2–1 e acompanhe o tempo restante. Use o guia mudo, o original a 20% ou música e efeitos do
  Demucs. Grave vários takes, ouça e escolha o ativo. É possível combinar IA e takes humanos
  na mesma mixagem, com ajuste de latência global e por fala.
- **Mixagem e saída.** Controle separadamente voz e trilha de fundo, ative o tratamento de
  áudio quando necessário e acompanhe o progresso. Compare o original com o dublado antes de
  exportar. A exportação direta preserva o fluxo de vídeo e gera MP4 ou MKV, conforme o codec,
  além do SRT. O editor de dublagem também permite cortes, clipes extras, transição por
  escurecimento, música e legendas ajustáveis.

No modo Gemini, use a chave configurada no Studio ou `GEMINI_API_KEY`. A transcrição envia
o áudio extraído ao Google. A tradução e o encurtamento enviam texto. Whisper, NLLB, Chatterbox,
Demucs e FFmpeg rodam na máquina. Use o modo offline após baixar os pesos locais.

[Passo a passo da dublagem e do ADR](docs/guia-studio.md#dublagem-de-vídeo)

## Editor de vídeo avançado

Abra **Edição → Editor de vídeo avançado**. Crie ou reabra uma montagem salva, importe
arquivos ou busque vídeos, áudio e SRT da biblioteca do Studio.

- **Enviar ao editor de vídeo** leva resultados de **Criar áudio**, **Dublagem**, **Projetos de
  narração**, **Histórias e personagens** e **Biblioteca de áudios** para um projeto novo ou existente.
- A **timeline** reúne vídeo principal, vídeo sobreposto, faixas de áudio, texto e legendas.
  Arraste clipes, aumente o zoom, ajuste cortes ou pressione **S** para dividir no cursor.
  O botão direito permite duplicar clipes e criar cópias de faixas de áudio.
- Vídeos com som entram com imagem e áudio vinculados. Adicione faixas extras, ajuste volumes
  e use **Efeitos de áudio** para configurar a altura do som de **−48 a +48 semitons** e graves,
  médios e agudos de **−12 a +12 dB**.
- Use **Adicionar texto** para títulos editáveis ou crie legendas na própria timeline.
  No vídeo principal, **Efeitos de vídeo** oferece ajuste HSL, ajuste Lab com inversão de
  luminosidade, mapa de gradiente, contração e expansão e redemoinho.
  Os efeitos são ajustáveis e combináveis.
  **Aplicar e gerar prévia** abre a montagem no trecho alterado.
- **Transições** permite dissolver, entrar a partir do preto ou deslizar o vídeo. Para áudio,
  ajuste a entrada e saída gradual ou mescle o clipe com o anterior da mesma faixa.
- Exporte **MP4 e SRT** em HD, Full HD ou formato vertical.

[Passo a passo da montagem](docs/guia-studio.md#editor-de-vídeo-avançado)

## Criação de áudio, modelos e treinamento

| Área | Recursos |
| --- | --- |
| **Criar áudio e regravar** | Referências gravadas ou importadas, voz por trecho, síntese F5-TTS ou Chatterbox, exportação WAV/MP3 e monitoramento da geração. |
| **Minhas vozes e biblioteca** | Referências salvas, forma de onda, cortes, busca, organização de arquivos e histórico de geração. |
| **Histórias e narração** | Elenco com vozes e modelos por personagem, revisão de blocos, pausas e exportação de áudio, roteiro e legendas conforme o projeto. |
| **Transcrição e ferramentas de áudio** | Whisper, tradução NLLB, revisão e exportação TXT/SRT, conversão, nivelamento e redução de ruído pelo FFmpeg. |
| **Revisão e consoles de IA** | Revisão com Gemini, OpenAI, Claude ou Ollama local. Análise de referência e geração de mídia pelos provedores disponíveis na interface. |
| **Meus modelos e treinamento** | Cadastro de checkpoints e vocabulários, comparação de versões, treino guiado com CUDA e exportação INT8 para CPU x86. |

O seletor no cabeçalho oferece português do Brasil, inglês, espanhol, francês, alemão,
chinês simplificado e japonês para a interface. Nas referências de voz, é possível medir
nível e clipping, recortar a gravação e transcrevê-la com Whisper. Use o painel de revisão
para testar sugestões antes de aplicar o roteiro. Os consoles de IA podem gerar mídia
quando o provedor, o modelo e a chave permitem. As chamadas em nuvem usam a cota do provedor.

Para usar BigVGAN, inicialize o submódulo a partir do clone editável:

```bash
git submodule update --init -- src/third_party/BigVGAN
```

[Guia completo do Studio](docs/guia-studio.md)

## Workspace, atualização e diagnóstico

Com o launcher, o workspace é `.studio/` e o cache do Hugging Face é
`.cache/huggingface/`. O comando `f5-tts_studio` sem `--workspace` usa
`~/.f5tts-studio/`. Mantenha a mesma pasta para reabrir suas montagens, modelos,
biblioteca e histórico. Os takes ficam em `outputs/dubbing/<projeto>/takes/<fala>/`
dentro do workspace. Os cortes e clipes das montagens são salvos no projeto.
Faça backup também de `data/` e `ckpts/` se você treina modelos.

Para atualizar no Linux ou WSL2, pare o processo em execução e use a mesma venv e o mesmo
workspace. Troque `cpu` pelo backend que instalou, caso use CUDA.

```bash
git pull --ff-only
uv pip install --python .studio/interface-python315/bin/python --torch-backend=cpu -e ".[studio]"
uv pip check --python .studio/interface-python315/bin/python
bash iniciar-studio.sh
```

No PowerShell, use o mesmo caminho `Scripts/python.exe` dos comandos de instalação.

| Sintoma | O que conferir |
| --- | --- |
| O navegador não abre o Studio | Mantenha o processo rodando e confira a porta exibida no terminal. Se estiver ocupada, use `--port 7872`. |
| O microfone não aparece | Abra pelo endereço `127.0.0.1` ou `localhost` e permita o uso do microfone no navegador. |
| CUDA indisponível | Confira `nvidia-smi` e `torch.cuda.is_available()` dentro da venv usada pelo Studio. |
| Falha ao carregar Chatterbox ou Demucs | Confira se a venv está dentro do workspace aberto e se os pesos do modelo estão disponíveis. |
| Texto ou legenda não aparece no vídeo | Confira `ffmpeg -filters` e a disponibilidade de `drawtext`, `subtitles` e fontes com os caracteres usados. |
| O Gemini não responde | Confira a chave, a cota, o modelo configurado e se o modo offline está desligado. |

### Validação

A instalação limpa em CPU no Linux/WSL2 foi verificada com Python 3.15.2 para Studio
e Chatterbox, e Python 3.12.16 para Demucs. A verificação incluiu dependências,
abertura das páginas de dublagem e edição, importação do Chatterbox, inicialização
do CLI do Demucs e uma tradução local com NLLB.

No ambiente do Studio, execute:

```bash
.studio/interface-python315/bin/python -m unittest discover -s tests -v
```

`testar-historias-navegador.py`, `testar-microfone-navegador.py` e
`testar-modelo-real.py` têm verificações opcionais da interface e da síntese, com
instruções nos próprios arquivos.

## Projeto original e créditos

O Studio usa **F5-TTS: A Fairytaler that Fakes Fluent and Faithful Speech with Flow Matching**.

- [Upstream repository](https://github.com/SWivid/F5-TTS)
- [Research paper](https://arxiv.org/abs/2410.06885)
- [Original demos](https://swivid.github.io/F5-TTS/)
- [Model repository](https://huggingface.co/SWivid/F5-TTS)


### Agradecimentos

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

### Citação
```
@article{chen-etal-2024-f5tts,
      title={F5-TTS: A Fairytaler that Fakes Fluent and Faithful Speech with Flow Matching},
      author={Yushen Chen and Zhikang Niu and Ziyang Ma and Keqi Deng and Chunhui Wang and Jian Zhao and Kai Yu and Xie Chen},
      journal={arXiv preprint arXiv:2410.06885},
      year={2024},
}
```
## Licença

O código usa a [licença MIT](LICENSE). Os pesos pré-treinados do F5-TTS têm licença CC BY-NC. Consulte a licença de cada modelo adicional para o seu uso.
