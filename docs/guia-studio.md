# Guia do F5 Studio 1.3.5

O F5 Studio roda localmente. Você pode gerar voz em WAV ou MP3, guardar referências, narrar
histórias com vozes por personagem, transcrever e traduzir legendas, editar áudios e ajustar
checkpoints F5-TTS para inferência.

- [Instalação e atualização](#instalação-e-atualização)
- [Primeiro áudio](#primeiro-áudio)
- [Treinamento de voz](#treinamento-de-voz)
- [BigVGAN](#bigvgan)
- [Quantização INT8](#quantização-int8)
- [Arquivos locais e backup](#arquivos-locais-e-backup)
- [Solução de problemas](#solução-de-problemas)

## Instalação e atualização

Use Python 3.11 a 3.15, FFmpeg com `ffprobe` e, no Linux, `libsndfile`. O README indica
Python 3.12 como ambiente de referência. A preparação de dados e a inferência podem usar CPU.
O treino pela interface usa uma GPU NVIDIA com PyTorch CUDA. Para o treinamento completo e
o BigVGAN, use uma cópia editável do repositório.

Em Linux ou WSL, instale FFmpeg e as bibliotecas do sistema conforme o [README](../README.md#requirements).
Depois clone o repositório, prepare o ambiente virtual e instale a distribuição do PyTorch adequada
ao hardware. Use a opção CUDA para treino e CPU para estações sem GPU. [Seletor oficial do PyTorch](https://pytorch.org/get-started/locally/)

```bash
git clone https://github.com/bolderfox12/F5-TTS.git
cd F5-TTS
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[studio]"
python -m pip check
bash iniciar-studio.sh
```

Abra **http://127.0.0.1:7862**. No Windows, use `py -3.12 -m venv .venv`, ative
`.venv\Scripts\Activate.ps1` e inicie com `f5-tts_studio`. O comando aceita `--no-browser`,
`--port` e `--workspace`.

Para atualizar uma instalação editável, use `git pull --ff-only` e
`python -m pip install -e ".[studio]"`. Reinicie com o mesmo launcher ou `--workspace`
para manter acesso aos dados anteriores. A primeira geração pode baixar os pesos do modelo
e do vocoder. Whisper e NLLB usam caches próprios dentro do cache de modelos do ambiente.

## Primeiro áudio

1. Abra **Criar áudio** e grave, envie ou selecione uma referência em **Minhas vozes**.
2. Informe a transcrição exata da referência para pular a transcrição automática.
3. Selecione o modelo, digite a frase, gere e confira o resultado antes de exportar.
4. Use **Histórico** para recuperar os áudios e **Biblioteca de áudios** para localizar, cortar ou
   organizar arquivos. **Histórias e personagens** permite atribuir voz e modelo por personagem.
   **Projetos de narração** trabalha com blocos e exporta áudio e legendas.

Uma voz salva reúne a referência e a transcrição. O modelo selecionado determina os pesos usados
para sintetizar o áudio e influencia a pronúncia do idioma.

### Chatterbox Multilingual V3 com Python 3.15

Na raiz do F5-TTS, crie um ambiente Python 3.15 no workspace do Studio e instale o
[Chatterbox oficial](https://github.com/resemble-ai/chatterbox) a partir do código-fonte:

```bash
python3.15 -m venv .studio/chatterbox/.venv
.studio/chatterbox/.venv/bin/python -m pip install --upgrade pip
.studio/chatterbox/.venv/bin/python -m pip install \
  "torch==2.15.0" "torchaudio==2.11.1" --index-url https://download.pytorch.org/whl/cpu
.studio/chatterbox/.venv/bin/python -m pip install \
  "chatterbox-tts @ git+https://github.com/resemble-ai/chatterbox.git@master"
.studio/chatterbox/.venv/bin/python -m pip check
.studio/chatterbox/.venv/bin/python -c "from chatterbox.mtl_tts import ChatterboxMultilingualTTS"
```

O exemplo instala PyTorch para CPU. Para CUDA, use o índice do PyTorch adequado à sua GPU.
O exemplo usa o workspace de `./iniciar-studio.sh`. Com outro `--workspace`, crie o
ambiente em `chatterbox/.venv` dentro dele. No Windows, use `py -3.15` para criar o
ambiente e `Scripts/python.exe` para executar os comandos dentro dele.

Em **Criar áudio** e **Regravar com outra voz**, escolha **Chatterbox Multilingual V3** e o idioma do texto.
Em **Histórias e personagens**, o modelo e o idioma são escolhidos por personagem.
Use uma referência de voz no mesmo idioma do texto. O primeiro uso baixa os pesos
do Hugging Face. O Chatterbox usa a configuração de dispositivo do Studio e seus
próprios parâmetros de síntese. A transcrição da referência, os passos de inferência
e a velocidade são usados pelo F5-TTS.

## Treinamento de voz

Abra **Treinar uma voz** e crie ou selecione um projeto. Para comparar experimentos, mantenha
um projeto separado por arquitetura, checkpoint inicial ou conjunto de dados.

### Preparar dados e vocabulário

1. Em **Dados e transcrições**, envie as gravações e confirme a autorização de uso. Selecione
   o idioma ou a detecção automática. O Studio usa Whisper para transcrever e pode segmentar
   gravações longas.
2. Para usar transcrições existentes, abra **Importar com transcrições prontas**. Cole uma linha
   para cada arquivo curto selecionado. Use os nomes originais dos arquivos.
3. Ouça os trechos, corrija o texto e salve a aprovação individual. O painel mostra RMS em
   dBFS e a proporção de amostras perto do limite de clipping para os clipes importados nesta versão.
4. Se usar um checkpoint customizado, selecione o vocabulário correspondente em **Treino e GPU**
   antes de clicar em **Preparar conjunto aprovado**. A opção de ampliar vocabulário preserva os
   IDs do modelo base e adiciona tokens dos textos aprovados.

```text
trecho-01.wav|Este é o primeiro trecho da sessão.
trecho-02.wav|A segunda gravação tem outra entonação.
```

Cada trecho deve ter de 1 a 30 segundos. Trechos de 3 a 12 segundos são um ponto de partida.
O mínimo para preparar são três exemplos aprovados somando 15 segundos. Para adaptar outro idioma,
use gravações claras, transcrições fiéis e frases variadas que cubram os sons e a escrita do idioma.
A ampliação do vocabulário permite treinar novos tokens. A pronúncia depende dos dados e do ajuste.
Para avaliar a generalização, reserve exemplos e teste também frases que ficaram fora do treino.

### Configurar o treino

| Controle | Efeito |
| --- | --- |
| Arquitetura e checkpoint inicial | Define os pesos e o vocoder compatíveis. Com o campo de checkpoint vazio, o Studio usa os pesos públicos da arquitetura escolhida. |
| Usar múltiplas GPUs CUDA | Desligado usa a primeira GPU visível. Ligado usa todas as GPUs CUDA visíveis nesse ambiente e pede pelo menos duas. Cada GPU carrega uma cópia do modelo. |
| Precisão BF16, FP16 ou total | A precisão mista pode reduzir o consumo de VRAM. Use BF16 em GPUs compatíveis. |
| Lote em frames e máximo de exemplos | Controlam o tamanho por GPU. O orçamento deve acomodar o trecho mais longo. |
| Acumulação e clipping de gradiente | A acumulação combina vários lotes menores por atualização. O clipping limita a norma do gradiente. |
| Adam de 8 bits, recomputação e TF32 | Adam de 8 bits reduz a memória do otimizador. No F5-TTS, recomputar ativações troca processamento por VRAM. TF32 acelera operações compatíveis em GPUs NVIDIA Ampere ou mais novas. |
| Cache de espectrogramas em RAM | Reutiliza mels já calculados nas épocas seguintes. Defina o limite total em GiB. O Studio divide esse limite por processo, limita o uso conforme a memória disponível e ajusta os workers de leitura para manter o cache. |
| Épocas, taxa, aquecimento e semente | Controlam a duração, o ritmo de ajuste, o aquecimento e a ordem dos dados. |
| Reserva para validação | Separa de 0 a 30% dos exemplos aprovados. Requer cinco ou mais exemplos para ativar. A cada época, o log registra a loss EMA em até 24 exemplos reservados. |
| Intervalo e retenção de checkpoints | Controlam a frequência e a quantidade de versões intermediárias. Checkpoints de treino incluem estado do otimizador. |

Clique em **Iniciar ou retomar treino**. A barra indica a época e as atualizações concluídas.
O **Monitor de recursos** mostra CPU, RAM, ocupação do volume de treino, taxa de leitura e gravação
do sistema, uso por GPU e VRAM. Temperatura e potência aparecem quando o driver disponibiliza os
dados. Use **Parar com segurança** para interromper. Para retomar, mantenha o conjunto preparado,
a quantidade de GPUs, o lote, a acumulação, a semente, a validação e o tipo de otimizador que o
checkpoint usou. Para alterar esses parâmetros, use outro projeto e carregue os pesos como base.

### Comparar e publicar

Em **Avaliar checkpoints**, selecione as versões e gere uma frase fixa com a mesma voz de referência.
Compare a pronúncia, a consistência da voz e defeitos audíveis. Registre a avaliação no projeto.
Em **Usar na inferência**, publique a versão escolhida. Ela passa a aparecer em **Meus modelos** e
pode ser selecionada em **Criar áudio**. Avaliações são anotações. Para ensinar uma correção,
adicione gravações adequadas a um projeto de ajuste.

## BigVGAN

No clone editável, inicialize o submódulo da NVIDIA:

```bash
git submodule update --init -- src/third_party/BigVGAN
```

Em **Treino e GPU → Arquitetura do checkpoint**, selecione **F5-TTS Base · BigVGAN**. A opção usa
o F5-TTS Base compatível com o vocoder BigVGAN v2 de 24 kHz, 100 bandas mel e hop de 256
amostras. O treino ajusta o modelo acústico. O gerador BigVGAN pré-treinado converte os
espectrogramas em áudio na comparação e na síntese. Ao cadastrar pesos BigVGAN manualmente,
escolha a mesma arquitetura em **Meus modelos**. [Implementação da NVIDIA](https://github.com/NVIDIA/BigVGAN)

## Quantização INT8

1. Abra **Quantização** no projeto de treino e escolha um checkpoint, ou selecione o modelo de
   origem em **Meus modelos**.
2. Dê um nome à cópia e exporte. O Studio informa o tamanho do arquivo original e do INT8.
3. Para transferir, baixe os pesos e o vocabulário, quando houver, em **Meus modelos**. Cadastre-os
   no outro computador com a arquitetura original.

O arquivo usa safetensors e quantiza as camadas lineares. A inferência seleciona CPU e precisa de um
backend x86 compatível no PyTorch, como x86, FBGEMM ou oneDNN. O vocoder mantém a precisão
original. O exemplo público F5-TTS v1 Base testado passou de cerca de 1,35 GB para 355 MB.
Compare o áudio no hardware de destino, pois tamanho, velocidade e qualidade variam.

## Arquivos locais e backup

| Caminho | Conteúdo |
| --- | --- |
| `~/.f5tts-studio/` | Workspace padrão de `f5-tts_studio`. |
| `.studio/` | Workspace usado por `bash iniciar-studio.sh`. Contém vozes, histórico, uploads e modelos gerenciados. |
| `data/` | Projetos de treinamento, manifestos, áudios e vocabulários em um clone editável. |
| `ckpts/` | Checkpoints do treino em um clone editável. |
| `.cache/huggingface/` | Cache de downloads quando iniciado pelo launcher local. |

Faça backup do workspace e das pastas de treinamento se quiser preservar o histórico, os dados
aprovados e os checkpoints. Para transportar apenas um modelo, leve os pesos e o vocabulário
que corresponde ao checkpoint. Confira também referências para arquivos cadastrados por caminho
fora do workspace.

## Solução de problemas

| Sintoma | O que verificar |
| --- | --- |
| CUDA indisponível | Confira `nvidia-smi` e se `torch.cuda.is_available()` retorna `True` dentro da mesma venv. |
| Falta de VRAM | Reduza os frames por GPU e o máximo de exemplos por lote. Use precisão mista e acumulação. No F5-TTS, habilite recomputação se precisar economizar VRAM. |
| Poucos lotes para muitas GPUs | Reduza o tamanho do lote, use uma GPU ou aumente a quantidade de dados preparados. |
| Memória do sistema alta | Reduza o limite do cache, desligue-o ou reduza os workers. |
| Vocabulário incompatível | Use o vocabulário base do checkpoint, prepare de novo e confira a arquitetura. Para acrescentar tokens após treinar, abra um projeto separado. |
| BigVGAN indisponível | Execute o comando de inicialização do submódulo e mantenha o clone editável. |
| Cópia INT8 não carrega | Confira o backend quantizado do PyTorch, a arquitetura e o arquivo de vocabulário. |
| Porta 7862 ocupada | Use `f5-tts_studio --port 7870` com o workspace desejado. |
| Download ou disco cheio | Verifique espaço para pesos, estados de otimizador, checkpoints intermediários e os modelos de transcrição e vocoder. |

A versão foi validada com 49 testes automatizados, navegação no Chromium, síntese BigVGAN na
RTX 3080 Ti e síntese INT8 em CPU x86. Um treino sintético de dois processos em CPU validou
lotes distribuídos, acumulação, validação e checkpoints. O ambiente de teste tinha uma única GPU
física. [Notas da versão](releases/v1.3.5.md)
