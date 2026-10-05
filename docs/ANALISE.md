# Análise técnica do repositório e plano de evolução

**Data:** outubro de 2026 · **Versão analisada:** 2.0.0 (commit `1cf1bf3`) · **Versão entregue:** 3.0.0

## 1. Objetivo

Verificar todo o repositório e identificar implementações, melhorias e correções para que **todo o fluxo funcione com o mínimo de intervenção manual**, aproveitando **ao máximo os recursos do FFmpeg** e **recursos gratuitos de alta qualidade**, com um **front-end adequado** para receber e entregar o conteúdo.

## 2. Estado encontrado (v2.0.0)

| Item | Situação |
|---|---|
| Código | Um único script, `src/tts_converter.py` (~120 linhas): classe `TTSConverter` com gTTS + reprodução via `ffplay`. |
| Entrada | Somente texto por argumento ou digitado no terminal. |
| Saída | Um MP3 (`audio.mp3`) sem tratamento algum. |
| Voz | Google Tradutor (gTTS), idioma e sotaque fixos no código (`pt`/`com.br`). |
| FFmpeg | Usado apenas para **tocar** o áudio (`ffplay`). |
| Testes | 4 testes `unittest` com mocks. |
| Infraestrutura | Sem empacotamento, sem CI, sem Docker, sem interface gráfica. |

## 3. Problemas encontrados

| # | Problema | Impacto | Correção na v3.0.0 |
|---|---|---|---|
| 1 | Dependência de um único serviço online e não oficial (gTTS), sem alternativa | Qualquer bloqueio, limite de requisições ou falta de internet interrompia todo o fluxo | Cadeia de motores com **fallback automático** Edge → Piper (offline) → gTTS → eSpeak, com 3 tentativas por trecho |
| 2 | Qualidade de voz limitada e sem controle de velocidade/tom | Áudio pouco natural para audiobooks, podcasts e vídeos | Vozes **neurais** gratuitas (Edge, Piper), velocidade nativa e tom via Rubber Band preservando formantes |
| 3 | FFmpeg subutilizado (só `ffplay`) | Volume inconsistente, sem metadados, sem formatos adequados a cada plataforma | Pipeline completo: SoX resampler, remoção de silêncios, pausas, EQ, de-esser, compressor, ducking, vinhetas, **loudnorm em 2 passagens**, capítulos, capa, legendas, vídeo, forma de onda e relatório |
| 4 | `-o arquivo.wav` gerava um MP3 com extensão `.wav` | Arquivo inválido para quem confia na extensão | Saída realmente codificada no formato pedido (MP3, M4B, M4A, Opus, FLAC, WAV) |
| 5 | Idioma/sotaque fixos no código | Impossível escolher voz ou idioma sem editar código | `--lang`, `--voice`, `--engine` e seleção na interface |
| 6 | Não lia arquivos | Usuário precisava copiar/colar o conteúdo | Leitura de TXT, Markdown, HTML, DOCX, ODT, EPUB e PDF, com detecção de codificação e capítulos |
| 7 | Textos longos enviados em uma única requisição, sem retomada | Falha perdia todo o trabalho; lento | Divisão em trechos por frases, **síntese paralela**, **cache** (re-execução instantânea) e novas tentativas |
| 8 | `logging.basicConfig()` executado ao importar o módulo | Efeito colateral em qualquer programa que importasse a classe | Logging configurado apenas pelo CLI |
| 9 | Erros de reprodução/síntese só registrados em log, CLI retornava sucesso | Automação não detectava falhas | Mensagens claras em português e **códigos de saída** (0, 1, 2, 130) |
| 10 | Testes criavam arquivos no diretório atual (`dummy.mp3`) e alteravam `sys.path` | Testes frágeis e sujos | Suíte `pytest` com `tmp_path`, motor de voz falso offline e **FFmpeg real** (71 testes) |
| 11 | Sem empacotamento nem comando instalável | Instalação manual e caminhos relativos | `pyproject.toml`, comando `tta`, extras `[web]`, `[pdf]`, `[piper]`, `[dev]` |
| 12 | Sem integração contínua | Regressões passariam despercebidas | GitHub Actions: lint, testes em Python 3.11–3.13 e build + smoke test do Docker |
| 13 | Sem interface gráfica | Uso restrito a quem domina terminal | Interface web responsiva com progresso em tempo real, player, transcrição sincronizada e histórico |
| 14 | Documentação inconsistente: badge v2.0.0 × CITATION 1.0.0; LICENSE com placeholders `[2023] [nome]`; Python 3.8+ (sem suporte desde 2024) | Informação incorreta para usuários e citações | README reescrito, LICENSE e CITATION corrigidos, Python 3.11+ |
| 15 | `.gitignore` incompleto | Risco de versionar ambientes virtuais e saídas | `.gitignore` e `.dockerignore` revisados |
| 16 | Instalação do FFmpeg manual e dependente do sistema | Principal fonte de “não funciona na minha máquina” | Imagem Docker com FFmpeg completo, eSpeak NG e voz Piper pré-instalada; `tta doctor` para diagnóstico |

## 4. O que foi implementado

### Automação (mínimo de intervenção manual)
- `docker compose up -d` sobe a **interface web** e a **pasta monitorada** (`data/entrada` → `data/saida`), com healthcheck e reinício automático.
- Pasta monitorada aceita **opções por arquivo** (`livro.json` ao lado de `livro.epub`) e separa sucessos e falhas (com o motivo em `.erro.txt`).
- **Fallback de motores**, **novas tentativas**, **cache** de trechos, **download automático** de vozes offline, **limpeza automática** de jobs antigos e do cache, retomada da lista de jobs após reinício.
- `tta doctor` verifica FFmpeg (filtros, encoders, SoX), motores, módulos opcionais e conectividade.

### Recursos do FFmpeg utilizados
`aresample` (SoX), `aformat`, `rubberband`, `atempo`, `asetrate`, `silenceremove`, `areverse`, `apad`, concat demuxer, `highpass`, `equalizer`, `deesser`, `acompressor`, `adelay`, `asplit`, `-stream_loop`, `sidechaincompress`, `amix`, `afade`, `concat`, `loudnorm` (2 passagens + medição final), `alimiter`, FFMETADATA (capítulos), `attached_pic` (capa), `libmp3lame`, `aac`, `libopus`, `flac`, `showwavespic`, `showwaves`, `showfreqs`, `gradients`, `boxblur`, `eq`, `loop`, `overlay`, `drawtext`, `subtitles` (libass), `libx264`, `ffprobe`. Detalhes em [`PIPELINE.md`](PIPELINE.md).

### Recursos gratuitos de alta qualidade
| Recurso | Uso |
|---|---|
| Microsoft Edge Neural (via `edge-tts`) | Vozes neurais pt-BR, sem chave de API |
| Piper (`piper-tts`) | Vozes neurais 100% offline e privadas |
| gTTS, eSpeak NG | Reservas |
| FFmpeg + Rubber Band + libsoxr + libass | Pós-produção, vídeo e legendas |
| FastAPI + HTML/CSS/JS puro | Interface sem etapa de build e sem CDN (funciona offline) |

### Front-end
- Formulário em 7 passos (conteúdo, perfil, voz, pós-produção, trilha, vídeo, metadados) com perfis que preenchem as opções automaticamente.
- Amostra de voz, contador de palavras e duração estimada, arrastar e soltar arquivos.
- Progresso em tempo real (Server-Sent Events com fallback para consulta periódica) e cancelamento.
- Player com forma de onda clicável, velocidade de reprodução, capítulos e **transcrição que destaca a frase falada**.
- Downloads individuais ou em ZIP, painel de qualidade (loudness medida × alvo, true peak, LRA, motor, cache).
- Histórico persistente, tema claro/escuro, layout responsivo (sem rolagem horizontal em 390 px), navegação por teclado e `aria-live`.

## 5. Verificação realizada

- **71 testes automatizados** passando (`pytest`), cobrindo extração de todos os formatos, divisão em frases, pronúncia, presets, motores (com serviços simulados), cada etapa do FFmpeg com o binário real (loudness medida dentro de ±1 LU do alvo, capítulos e capa conferidos com `ffprobe`, vídeo nas três orientações), pipeline completo, fallback, cache, cancelamento, CLI, pasta monitorada e API web (inclusive Range, ZIP, SSE e proteção contra path traversal).
- `ruff check` e `ruff format --check` sem pendências.
- Teste ponta a ponta da interface em navegador real (Chromium/Playwright): envio de texto, progresso, conclusão, reprodução com destaque da transcrição, downloads, painel de qualidade, tema escuro e layout móvel, sem erros de JavaScript.
- **Limitação:** o ambiente onde a v3.0.0 foi desenvolvida bloqueia o acesso aos serviços de voz (Microsoft, Google, Hugging Face). Por isso a síntese real foi validada com serviços simulados; recomenda-se rodar `tta doctor --online` e um `tta synth -t "teste"` na primeira instalação. A imagem Docker também não pôde ser construída localmente (é validada pelo CI).

## 6. Recomendações para as próximas versões

| Prioridade | Sugestão | Benefício |
|---|---|---|
| Alta | Autenticação (ou publicar só atrás de proxy com login) antes de expor a interface na internet | Segurança: hoje qualquer pessoa com acesso à porta pode gerar áudios e ver o histórico |
| Alta | Teste periódico (agendado no CI) da síntese real com Edge/gTTS | Detectar rapidamente mudanças nos serviços não oficiais |
| Média | Motor **Kokoro-82M** (Apache-2.0, vozes pt-BR) via `kokoro-onnx` | Mais uma voz offline de altíssima qualidade |
| Média | Normalização de números, datas, moedas e siglas para motores offline (`num2words` pt_BR) | Leitura mais natural no Piper/eSpeak |
| Média | Marcações no texto (`[pausa 2s]`, ênfase, troca de voz por personagem) convertidas em SSML | Narrativas com mais de um narrador e controle fino |
| Média | OCR automático de PDFs digitalizados (`ocrmypdf`/Tesseract) | Elimina mais uma etapa manual |
| Média | Feed RSS de podcast gerado a partir dos jobs e publicação opcional no YouTube | Distribuição automática |
| Baixa | Fila distribuída (Redis/RQ) e armazenamento em S3/MinIO | Vários servidores e muitos usuários |
| Baixa | Azure AI Speech (camada gratuita oficial) como motor adicional | Alternativa oficial para uso comercial |
| Baixa | Clonagem de voz (F5-TTS/XTTS) — atenção às licenças não comerciais | Voz personalizada do autor |
