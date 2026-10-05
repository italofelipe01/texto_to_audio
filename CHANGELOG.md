# Changelog

Todas as mudanças relevantes deste projeto. Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [3.0.0] - 2026-10-05

Reescrita completa como pipeline automatizado de texto para áudio. Análise detalhada em [`docs/ANALISE.md`](docs/ANALISE.md).

### Adicionado
- Pacote `texto_to_audio` instalável (`pyproject.toml`) com o comando `tta` (`synth`, `voices`, `doctor`, `presets`, `watch`, `serve`, `download-voice`, `cache`).
- Motores de voz Edge Neural, Piper (offline), gTTS e eSpeak NG com fallback automático, novas tentativas e cache de trechos.
- Leitura de TXT, Markdown, HTML, DOCX, ODT, EPUB e PDF, com capítulos a partir dos títulos e dicionário de pronúncia.
- Pós-produção com FFmpeg: SoX resampler, Rubber Band, remoção de silêncios e pausas, EQ/de-esser/compressor, trilha com ducking, vinhetas, loudnorm em duas passagens, capítulos, capa e metadados.
- Saídas MP3, M4B, M4A, Opus, FLAC, WAV, legendas SRT/WebVTT, transcrição sincronizada, forma de onda, vídeo MP4 (16:9, 9:16, 1:1) e relatório de qualidade.
- Perfis prontos: padrão, audiobook, podcast, acessibilidade, vídeo, shorts e rascunho.
- Interface web (FastAPI + HTML/CSS/JS) com progresso em tempo real, player com transcrição sincronizada, histórico e API REST documentada.
- Pasta monitorada com opções por arquivo.
- Dockerfile e docker-compose (web + pasta monitorada), GitHub Actions (lint, testes 3.11–3.13, build Docker), Makefile e `.env.example`.
- 71 testes automatizados que rodam sem internet.

### Corrigido
- `-o arquivo.wav` (ou outra extensão) agora gera de fato o formato pedido, e não um MP3 renomeado.
- Logging deixou de ser configurado na importação do módulo.
- Falhas agora retornam código de saída diferente de zero.
- Testes não criam mais arquivos no diretório atual.
- LICENSE (placeholders), CITATION (versão/data) e requisitos de Python (3.11+) corrigidos.

### Compatibilidade
- `python src/tts_converter.py "texto" [-o arquivo] [--no-play]` continua funcionando, agora sobre o novo pipeline.
- A classe `TTSConverter` foi substituída pelo pacote `texto_to_audio` (use `Pipeline` ou o CLI).

## [2.0.0] - 2025-12-17

- Estrutura em classe (`TTSConverter`), CLI com `argparse`, reprodução via `ffplay` e testes unitários.

## [1.0.0] - 2023-11-01

- Primeira versão: conversão de texto em áudio com gTTS.
