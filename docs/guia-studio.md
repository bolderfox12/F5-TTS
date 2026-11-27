# Guia do F5 Studio

O F5 Studio roda localmente. Você pode gerar voz em WAV ou MP3, guardar referências, narrar
histórias com vozes por personagem, transcrever e traduzir legendas, dublar vídeos com IA
ou gravação humana, editar a montagem e ajustar checkpoints F5-TTS para inferência.

- [Instalação e atualização](#instalação-e-atualização)
- [Primeiro áudio](#primeiro-áudio)
- [Dublagem de vídeo](#dublagem-de-vídeo)
- [Editor de vídeo avançado](#editor-de-vídeo-avançado)
- [Treinamento de voz](#treinamento-de-voz)
- [BigVGAN](#bigvgan)
- [Quantização INT8](#quantização-int8)
- [Arquivos locais e backup](#arquivos-locais-e-backup)
- [Solução de problemas](#solução-de-problemas)

## Instalação e atualização

A instalação de referência usa Python 3.15, FFmpeg com FFprobe e ambientes separados
para o Studio, Chatterbox e Demucs. Consulte os comandos para Linux/WSL2 e Windows
PowerShell no [README](../README.md#instalação).

O launcher `bash iniciar-studio.sh` usa `.studio/interface-python315` e inicia em
[http://127.0.0.1:7862](http://127.0.0.1:7862). Para apontá-lo para outro ambiente
Python 3.15 com o Studio instalado, defina `F5_STUDIO_UI_PYTHON`. O comando também
aceita `--no-browser`, `--port` e `--workspace`.

Ao atualizar, mantenha o mesmo workspace para preservar montagens, biblioteca,
modelos e histórico. Prepare os dados e faça inferência em CPU ou, conforme o modelo,
use CUDA. O treino guiado requer GPU NVIDIA com PyTorch CUDA. Para BigVGAN e
treinamento completo, use um clone editável.

## Primeiro áudio

Em **Configurações**, escolha **NVIDIA / CUDA** e defina **Precisão na GPU**
como **FP16** ou **FP32**. A opção vale para o F5-TTS compatível em CUDA.

1. Abra **Criar áudio** e grave, envie ou selecione uma referência em **Minhas vozes**.
2. Informe a transcrição exata da referência para pular a transcrição automática.
3. Selecione o modelo, digite a frase, gere e confira o resultado antes de exportar.
4. Use **Histórico** para recuperar os áudios e **Biblioteca de áudios** para localizar, cortar ou
   organizar arquivos. **Histórias e personagens** permite atribuir voz e modelo por personagem.
   **Projetos de narração** trabalha com blocos e exporta áudio e legendas.

Uma voz salva reúne a referência e a transcrição. O modelo selecionado determina os pesos usados
para sintetizar o áudio e influencia a pronúncia do idioma.

### Chatterbox Multilingual V3 com Python 3.15

Siga a [instalação do Chatterbox no README](../README.md#chatterbox-multilingual-v3).
Crie o ambiente em `chatterbox/.venv` dentro do workspace que pretende usar.

Em **Criar áudio** e **Regravar com outra voz**, escolha **Chatterbox Multilingual V3** e o idioma do texto.
Em **Histórias e personagens**, o modelo e o idioma são escolhidos por personagem.
Use uma referência de voz no mesmo idioma do texto. O primeiro uso baixa os pesos
do Hugging Face. O Chatterbox usa a configuração de dispositivo do Studio e seus
próprios parâmetros de síntese. A transcrição da referência, os passos de inferência
e a velocidade são usados pelo F5-TTS.

### Fish Speech S2-Pro na dublagem

O S2-Pro pode ser escolhido em **Dublagem por IA**. Ele usa um servidor Fish Speech local
separado, que precisa estar ativo em `http://127.0.0.1:8080`; veja a
[instalação e os comandos no README](../README.md#fish-speech-s2-pro-na-dublagem). A referência
de cada personagem deve ter a transcrição preenchida. A geração usa a API local Fish Speech;
ela não envia o áudio para a nuvem por padrão. O tamanho de memória de GPU recomendado pelo
Fish é 24 GB, e placas diferentes não compartilham VRAM automaticamente.

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

A versão 1.3.5 foi validada com 49 testes automatizados, navegação no Chromium, síntese BigVGAN na
RTX 3080 Ti e síntese INT8 em CPU x86. Um treino sintético de dois processos em CPU validou
lotes distribuídos, acumulação, validação e checkpoints. O ambiente de teste tinha uma única GPU
física. [Notas da versão](releases/v1.3.5.md)

### Dublagem de vídeo

O menu **Dublagem** tem **Dublagem por IA** e **Dublagem convencional com
humanos**. Alterne pelos botões ou pelo menu lateral enquanto o projeto estiver
aberto para manter vídeo, roteiro, áudios de IA e takes.

1. Envie um MP4, MOV, MKV ou WebM de até 500 MB. Selecione o idioma original
   e um modelo de transcrição. Whisper Small e Whisper Turbo rodam localmente.
   **Gemini · transcrição e personagens** envia o áudio extraído ao Google e
   reutiliza a chave Gemini do Studio ou aceita uma chave para a sessão.
   **Revisar trechos sem transcrição** faz uma segunda passada em intervalos
   com atividade sonora. Revise os cartões marcados como **Fala recuperada**.
   A prévia do
   vídeo original aparece após o envio.
2. Clique em **Transcrever e separar vozes**. Revise o texto e corrija o número
   do personagem em cada fala. Ouça a amostra original de cada personagem e
   escolha outro trecho de referência, se necessário.
3. Escolha o idioma de destino e o modelo de dublagem. O Chatterbox
   Multilingual V3 usa as referências originais para sintetizar a fala de cada
   personagem. Você também pode selecionar um modelo de voz registrado no Studio
   que seja compatível com o idioma escolhido.
4. Selecione **NLLB · tradução local** ou **Gemini · tradução do roteiro**.
   A opção Gemini envia o texto do roteiro ao Google e reutiliza a mesma chave
   da transcrição. Clique em **Traduzir roteiro**.
5. No **Editor e revisão de dublagem**, clique em uma fala para ir até o
   início dela no player. Ajuste os tempos, os textos ou o personagem e gere
   as prévias. Use **Regerar fala** para atualizar somente o trecho alterado.
   O indicador mostra a duração excedente. O ajuste automático acelera a fala
   até 1,30×. Se o trecho ainda exceder a janela, encurte o texto ou ajuste os
   tempos antes da exportação. **Encurtar fala com Gemini** propõe sinônimos
   e expressões mais breves com base na janela e na duração medida. Escolha
   uma sugestão e regenere a fala para conferir a duração antes de exportar.
6. Em **Mixagem e exportação**, ajuste os volumes do fundo e da voz. O Demucs
   separa música e efeitos da voz original. Clique em **Gerar prévia dublada**
   para ouvir a mixagem e conferir o vídeo antes de clicar em **Exportar vídeo
   dublado**. A barra no canto da tela mostra a etapa atual de transcrição,
   síntese, mixagem ou renderização. **Encurtar e sincronizar falas com Gemini
   na prévia** tenta ajustar as falas que excedem o tempo, inclusive em **Gerar
   prévia editada**. O Studio mede a síntese de cada alternativa e só aplica
   uma versão que caiba na janela com a velocidade permitida.
   A exportação direta preserva o fluxo de vídeo original e usa MP4 ou MKV,
   conforme a compatibilidade do codec.
7. Após exportar um MP4, use o editor de montagem para comparar os vídeos,
   cortar o trecho, juntar outros clipes de até 500 MB por arquivo e adicionar transições, música ou
   legendas. **Gerar prévia editada** disponibiliza o MP4 e o SRT da montagem.

A dublagem aceita português, inglês, espanhol, francês, alemão, coreano,
japonês e chinês (mandarim, com tradução em caracteres simplificados). O
processamento é local e requer FFmpeg, o ambiente Chatterbox e os pesos dos
modelos. O editor usa FFprobe e a incorporação de legendas requer FFmpeg com libass. O primeiro uso pode baixar pesos, inclusive os do tradutor NLLB.
A separação agrupa os trechos reconhecidos e pode precisar de correções, em
especial quando há vozes sobrepostas. A separação de fundo por Demucs também pode
deixar resíduos da voz original em áudios difíceis.

#### Instalar o Demucs para preservar o fundo

Use o [ambiente separado do Demucs](../README.md#demucs-para-música-e-efeitos).
Os pesos são baixados no primeiro uso. Com o volume de fundo em 0%, a separação
não é necessária. Os WAVs e exportações de cada revisão ficam em
`<workspace>/outputs/dubbing/<id-do-projeto>`. A revisão é aberta na sessão da
página.

#### Gravar takes humanos e combinar com IA

1. Abra **Dublagem convencional com humanos** e envie o vídeo. Use a transcrição
   como guia ou clique em **Adicionar fala** para marcar um trecho manualmente.
2. Clique em **Ativar e testar microfone**, selecione o dispositivo e observe o
   medidor. Escolha o guia mudo, o original a 20% ou a faixa M&E separada pelo
   Demucs. Prepare a faixa M&E antes de selecioná-la.
3. Ajuste os tempos da fala e clique em **Gravar com contagem (3-2-1)**. O vídeo
   recua até três segundos, toca os bipes e a contagem visual acompanha o
   tempo restante da fala. Ouça os takes e selecione qual entra na mixagem.
4. Volte à IA para sintetizar as outras falas, se desejar. O take ativo de cada
   fala tem prioridade na exportação. Use **Usar voz de IA nesta fala** para
   voltar à síntese, sem excluir o take gravado.
5. Ajuste, se necessário, a compensação de latência global e o ajuste fino de
   cada fala. Valores negativos adiantam a voz gravada. O tratamento opcional
   reduz ruído, filtra graves, comprime picos e normaliza o nível.
6. Gere a prévia e exporte. Os takes são padronizados em WAV mono de 48 kHz e
   guardados por projeto e fala em `outputs/dubbing/<projeto>/takes/<fala>`.


### Editor de vídeo avançado

Abra **Edição → Editor de vídeo avançado**. Crie um projeto ou abra uma
montagem salva. As telas **Criar áudio**, **Dublagem**, **Projetos de narração**,
**Histórias e personagens** e **Biblioteca de áudios** também oferecem
**Enviar ao editor de vídeo** para iniciar uma montagem ou adicionar ao fim
de uma existente.

Busque vídeos, áudios e SRT já presentes no Studio, importe mídias ou crie
legendas na própria tela. Clique na régua da linha do tempo para marcar um
ponto e adicione o arquivo no cursor. Arraste os clipes para mudar sua posição.
Use o cartão de edição para cortar, dividir, mudar de faixa ou ajustar o volume.
Pressione **S** para dividir no cursor. Clique com o botão direito no clipe
para duplicá-lo. No nome da faixa de áudio, **Duplicar faixa** cria uma faixa
nova com cópias dos clipes. Também é possível criar faixas vazias com
**Adicionar faixa de áudio**.
Vídeos com som entram com imagem e áudio vinculados. O áudio tem pitch
ajustável de −48 a +48 semitons, sem mudar a duração da faixa. Clique com o
botão direito no áudio e escolha **Efeitos de áudio** para ajustar a altura do som,
graves, médios e agudos por clipe. Em projetos antigos,
selecione um vídeo com som e use **Separar áudio do vídeo**.
Use **Adicionar texto** para escrever um título editável na faixa TX.

A faixa V1 é o vídeo principal e V2 aparece sobreposta no canto. A1 e A2 podem
combinar voz, música e efeitos. A faixa CC incorpora as legendas e gera o SRT.
No clipe da faixa V1, use **Efeitos de vídeo** pelo botão direito ou pela
barra acima da timeline. Ajuste HSL, Lab com inversão de luminosidade,
mapa de gradiente, contração e expansão e redemoinho separadamente. Os efeitos podem ser
combinados. Alterar um controle ativa o efeito automaticamente. Use
**Aplicar e gerar prévia** para conferir o resultado no trecho do clipe.

Em **Transições**, selecione o clipe de entrada e escolha dissolução,
entrada a partir do preto ou deslizamento. Para uma passagem entre dois
clipes de vídeo, ative a sobreposição com o clipe anterior.
Nas faixas de áudio, ajuste a entrada e a saída gradual individualmente
ou use **Mesclar com o áudio anterior** para criar uma transição cruzada.
A mescla move o clipe para criar a sobreposição e aplica os dois ajustes de volume.

As configurações oferecem HD, Full HD e formato vertical. Gere uma prévia e
exporte MP4 e SRT quando estiver pronto.

### Console de IA para vídeos do YouTube

No **Editor de vídeo avançado**, abra um projeto e clique em **Console de IA** acima da linha do tempo. Escolha **Gemini**, usando a chave cadastrada nas configurações, ou **Ollama local**, selecionando um modelo instalado, como Qwen.

Digite um comando ou escolha **Gravar comando** e depois **Transcrever comando**. Confira o texto antes de clicar em **Sugerir alterações**. Use nomes de clipes, a seleção atual e tempos da linha do tempo para indicar os trechos.

- Peça cortes, ajustes de volume, fades, mesclas de áudio, dissoluções, legendas com texto informado, títulos e chamadas para inscrição.
- Configure 1080p horizontal para o YouTube ou o formato vertical para Shorts.
- Informe o tema e os tempos dos trechos para receber sugestões de título, descrição, texto de capa e capítulos.

Confira as mudanças listadas e clique em **Aplicar e gerar prévia**. O console trabalha com os dados da linha do tempo. Para indicar onde cortar uma fala ou cena, informe o tempo do trecho.

### Estúdio YouTube

O menu **Estúdio YouTube** permite salvar o nome, nicho, público, palavras monitoradas, idioma e região de cada canal. Brasil, português e fuso de Brasília são usados como padrões. Adicione pautas manualmente ou configure uma chave da YouTube Data API e use **Buscar agora** para consultar vídeos recentes. Cada resultado conserva título, link, canal de origem e data informada pela API. Pautas repetidas no mesmo canal são deduplicadas.

Esta primeira etapa salva perfis e oportunidades no workspace local. A coleta automática, roteiro por cenas, geração de mídia e voz, montagem assistida, OAuth, envio e calendário entram nas próximas etapas. A chave da API fica no arquivo local de credenciais do Studio e nenhuma publicação é feita por esta tela.

Ao abrir **Roteiro e cenas** em uma pauta, escolha Gemini ou Ollama e gere um roteiro estruturado com ângulo, promessa, fatos a confirmar, narração e direção visual por cena. Revise o JSON e use **Salvar roteiro e montar timeline** para criar um projeto normal do editor, com narração como legendas de referência e textos de tela em suas faixas. A voz e as mídias das cenas serão adicionadas nas próximas etapas.

### Voz local expressiva

Na tela **Criar áudio**, escolha uma intenção narrativa na revisão de roteiro para orientar o ritmo e a pontuação com o modelo de revisão selecionado. Para modelos F5-TTS, ajuste a velocidade de 0,5× a 2× por geração. O Chatterbox mantém os controles próprios do modelo. Depois da síntese, abra **Conferir pronúncia e texto com Whisper** para comparar a fala reconhecida com o roteiro e ouvir o resultado.

No **Estúdio YouTube**, a área **Narração por cena** usa referências de voz salvas e os modelos locais já disponíveis. Cada fala é gerada individualmente, colocada na faixa A1 e alinhada pela duração real. Regerar uma cena atualiza esse trecho e reposiciona as cenas seguintes.
