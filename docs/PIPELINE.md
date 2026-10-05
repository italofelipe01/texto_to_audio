# Pipeline de áudio com FFmpeg

Este documento descreve, etapa por etapa, o que acontece entre o texto e os arquivos finais, e mostra os comandos FFmpeg equivalentes (simplificados) para quem quiser reproduzir ou ajustar o processo. O código está em [`src/texto_to_audio/ffmpeg.py`](../src/texto_to_audio/ffmpeg.py) e é orquestrado por [`pipeline.py`](../src/texto_to_audio/pipeline.py).

## 1. Texto → trechos

1. O arquivo é convertido em um documento com **seções** (capítulos) e **parágrafos** (`text.py`).
2. O texto é limpo para fala: Unicode NFC, URLs viram só o domínio, emojis e marcadores são removidos, Markdown é desfeito.
3. Cada parágrafo é dividido em frases (com tratamento de abreviações como “Sr.”, “pág.”, “Dr.” e iniciais).
4. Frases são agrupadas em **trechos de até 400 caracteres** que nunca atravessam parágrafos. Isso permite paralelismo, novas tentativas granulares, cache e pausas corretas.
5. O dicionário de pronúncia é aplicado **apenas ao texto falado**; legendas e transcrição usam o texto original.

## 2. Síntese com cache e fallback

- Cada trecho é sintetizado em paralelo (até `TTA_MAX_WORKERS`, respeitando o limite de cada motor).
- A chave de cache é `sha256(motor+versão, voz, velocidade, texto falado)`; trechos repetidos ou de execuções anteriores não são sintetizados de novo.
- Até 3 tentativas por trecho (esperas de 1,5 s e 3 s). Se um motor falhar de vez, o job inteiro passa para o próximo motor da cadeia `edge → piper → gtts → espeak`.

## 3. Preparação de cada trecho

```bash
ffmpeg -i trecho.mp3 -af "\
aresample=48000:resampler=soxr:precision=28,\
aformat=sample_fmts=s16:channel_layouts=mono,\
rubberband=pitch=1.05946:formant=preserved:pitchq=quality,\
rubberband=tempo=1.1000:pitchq=quality,\
silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB:start_silence=0.06,areverse,\
silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB:start_silence=0.06,areverse" \
  -c:a pcm_s16le fala.wav

ffmpeg -i fala.wav -af "apad=pad_dur=0.6" -c:a pcm_s16le trecho_final.wav
```

- **SoX resampler**: conversão de taxa de amostragem de alta qualidade (usada quando o FFmpeg foi compilado com libsoxr; senão, `aresample` padrão).
- **Rubber Band**: muda tom preservando formantes (a voz continua natural) e muda velocidade sem alterar o tom. Sem Rubber Band, usa `asetrate` + `atempo`.
- A velocidade só é aplicada aqui para motores sem controle nativo (gTTS); Edge, Piper e eSpeak recebem a velocidade direto na síntese, o que soa mais natural.
- **Silêncios**: remove o silêncio das pontas (o truque `areverse` corta o final sem mexer nas pausas internas) e adiciona uma pausa uniforme conforme o tipo de quebra: frase (0,2 s), parágrafo (0,6 s), capítulo (1,4 s).
- A duração real de cada trecho é medida para montar a linha do tempo de legendas e capítulos.

## 4. União sem perdas

```bash
# lista.txt: file '/abs/trecho_00000.wav' ...
ffmpeg -f concat -safe 0 -i lista.txt -c copy voz.wav
```

## 5. Tratamento, trilha e vinhetas (um único filtergraph)

```bash
ffmpeg -i voz.wav -stream_loop -1 -i trilha.mp3 -i vinheta.mp3 -filter_complex "\
[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,\
  highpass=f=70,equalizer=f=250:t=q:w=1.0:g=-1.5,equalizer=f=3500:t=q:w=1.2:g=2,\
  deesser=i=0.4:m=0.5:f=0.5,acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=1,\
  adelay=delays=2000:all=1,apad=pad_dur=3,asplit=2[voz][sc];\
[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=-20dB,afade=t=in:d=2[musica];\
[musica][sc]sidechaincompress=threshold=0.015:ratio=12:attack=20:release=700[fundo];\
[voz][fundo]amix=inputs=2:duration=first:normalize=0,afade=t=out:st=DURACAO-3:d=3[principal];\
[2:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[intro];\
[intro][principal]concat=n=2:v=0:a=1[out]" -map "[out]" -c:a pcm_f32le premaster.wav
```

| Filtro | Papel |
|---|---|
| `highpass=f=70` | Remove ruídos graves (rumble) |
| `equalizer` 250 Hz −1,5 dB | Reduz “embolamento” |
| `equalizer` 3,5 kHz +2 dB | Presença e inteligibilidade |
| `deesser` | Suaviza sibilantes (“s”, “x”) |
| `acompressor` 3:1 | Uniformiza a dinâmica da fala |
| `-stream_loop -1` | Repete a trilha pelo tempo que for preciso |
| `sidechaincompress` | **Ducking**: a voz controla o compressor da música, que abaixa ~12:1 enquanto há fala |
| `adelay` + `apad` | 2 s de música antes da voz e 3 s depois, com `afade` de saída |
| `concat` | Vinhetas de abertura/encerramento |

O áudio intermediário é salvo em ponto flutuante (`pcm_f32le`) para não haver clipping antes da normalização.

## 6. Loudness EBU R128 em duas passagens

```bash
# passagem 1: medir
ffmpeg -i premaster.wav -af loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json -f null -
# passagem 2: aplicar com os valores medidos (modo linear = sem compressão extra)
ffmpeg -i premaster.wav -af "loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=-23.1:measured_TP=-4.2:\
measured_LRA=5.3:measured_thresh=-33.4:offset=0.3:linear=true,aresample=48000" master.wav
```

Alvos usados pelos perfis:

| Destino | Integrado | True peak |
|---|---|---|
| YouTube, Spotify, redes sociais | −14 LUFS | −1 dBTP |
| Podcast (Apple/Spotify) | −16 LUFS | −1 a −1,5 dBTP |
| Audiobook (ACX/Audible) | −18 LUFS | −3 dBTP |
| Rádio/TV (EBU R128) | −23 LUFS | −1 dBTP |

Ao final, o master é medido de novo e os valores aparecem no relatório e no painel “Qualidade”.

## 7. Codificação, metadados, capítulos e capa

```bash
# metadata.txt
;FFMETADATA1
title=O Farol da Ilha
artist=Texto para Áudio
[CHAPTER]
TIMEBASE=1/1000
START=0
END=61500
title=Capítulo 1 — A chegada

ffmpeg -i master.wav -i metadata.txt -i capa.jpg -map 0:a -map 2:v -c:v copy \
  -disposition:v:0 attached_pic -map_metadata 1 -map_chapters 1 \
  -c:a libmp3lame -b:a 96k -id3v2_version 3 -write_id3v1 1 livro.mp3

ffmpeg -i master.wav -i metadata.txt -i capa.jpg -map 0:a -map 2:v -c:v copy \
  -disposition:v:0 attached_pic -map_metadata 1 -map_chapters 1 \
  -c:a aac -b:a 80k -f ipod -movflags +faststart livro.m4b
```

| Formato | Codec | Bitrate (mono/estéreo) | Capa | Capítulos |
|---|---|---|---|---|
| MP3 | libmp3lame | 96k / 192k | ✔ | ✔ (ID3 CHAP) |
| M4B | AAC | 80k / 128k | ✔ | ✔ |
| M4A | AAC | 96k / 160k | ✔ | ✔ |
| Opus | libopus (`-application voip` para fala) | 48k / 96k | — | ✔ |
| FLAC | flac nível 8 | sem perdas | ✔ | — |
| WAV | PCM 16 bits | sem compressão | — | — |

A capa é recortada para 1400×1400 (exigência de lojas de podcast/audiobook).

## 8. Legendas e transcrição

- O início de cada trecho é conhecido (soma das durações medidas + deslocamento de vinheta/trilha).
- Dentro do trecho, as frases são posicionadas pelos limites de frase informados pelo Edge TTS; nos demais motores, proporcionalmente ao número de caracteres.
- Frases longas viram várias legendas de até 2 linhas × 42 caracteres, com duração mínima de 0,7 s e sem sobreposição.

## 9. Forma de onda e vídeo

```bash
ffmpeg -i master.wav -filter_complex \
  "aformat=channel_layouts=mono,showwavespic=s=1600x240:colors=0x3b82f6:scale=sqrt:filter=peak" \
  -frames:v 1 onda.png

ffmpeg -i master.wav -i fundo.png -filter_complex "\
[1:v]format=yuv420p,loop=loop=-1:size=1:start=0,settb=1/25,setpts=N/25/TB[bg];\
[0:a]aformat=channel_layouts=mono,showwaves=s=1536x280:mode=cline:rate=25:colors=0x38bdf8:scale=sqrt:draw=full,format=rgba[viz];\
[bg][viz]overlay=(W-w)/2:(H-h)/2:shortest=1:format=yuv420[v1];\
[v1]drawtext=textfile=titulo0.txt:fontfile=DejaVuSans-Bold.ttf:fontcolor=white:fontsize=64:x=(w-text_w)/2:y=86[v2];\
[v2]subtitles=subs.srt:force_style='FontName=DejaVu Sans,FontSize=16,Outline=2,Alignment=2,MarginV=24'[vout]" \
  -map "[vout]" -map 0:a -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p \
  -c:a aac -b:a 160k -shortest -movflags +faststart video.mp4
```

Otimizações: o fundo (gradiente gerado pelo filtro `gradients`, ou a imagem enviada com desfoque e escurecimento) é renderizado **uma única vez** e repetido com `loop`, evitando animar ou desfocar o fundo quadro a quadro (nos testes, o custo do fundo caiu de ~3,7× para ~1× o de um fundo de cor sólida). Cada linha do título é um `drawtext` próprio para ficar centralizada em qualquer versão do FFmpeg.

| Orientação | Resolução | Uso |
|---|---|---|
| `landscape` | 1920×1080 | YouTube, apresentações |
| `portrait` | 1080×1920 | Reels, Shorts, TikTok, status |
| `square` | 1080×1080 | Feed do Instagram/LinkedIn |
