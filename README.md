# Texto para Áudio (TTS Converter)

![Version](https://img.shields.io/badge/version-v2.0.0-blue?style=for-the-badge)
![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)
![Status](https://img.shields.io/badge/status-stable-success?style=for-the-badge)
![Python](https://img.shields.io/badge/python-3.8%2B-blueviolet?style=for-the-badge)

## 📄 Descrição
O **Texto para Áudio** é uma solução de software robusta desenvolvida para converter strings de texto em arquivos de áudio falado (Text-to-Speech) utilizando a API do Google Translate (gTTS). O sistema automatiza a síntese de voz em Português Brasileiro e a reprodução imediata do conteúdo gerado.

## 🚀 Funcionalidades
- **Síntese de Voz:** Conversão de texto livre para áudio em Português (`pt-br`).
- **Reprodução Automática:** Integração segura com `ffplay` (FFmpeg) para execução imediata do áudio gerado.
- **Exportação de Arquivo:** Gera e salva o arquivo de áudio (padrão `audio.mp3`) no diretório local.
- **Interface de Linha de Comando (CLI):** Suporte a argumentos via terminal e modo interativo.
- **Estrutura Modular:** Código organizado em classes para fácil manutenção e integração.

## 🛠 Tecnologias Utilizadas
- **Linguagem:** Python 3.8 ou superior.
- **Bibliotecas:**
  - `gTTS` (Google Text-to-Speech).
- **Dependência de Sistema:** FFmpeg (necessário para o comando `ffplay`).

## 📦 Instalação

1. **Clone o repositório:**
```bash
git clone https://github.com/italofelipe01/texto_to_audio.git
cd texto_to_audio
```

2. **Crie um ambiente virtual (opcional, mas recomendado):**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows
```

3. **Instale as dependências do Python:**
```bash
pip install -r requirements.txt
```

4. **Instale o FFmpeg (Requisito de Sistema):**
   - Certifique-se de que o `ffmpeg` está instalado e acessível no PATH do sistema.
   - **Ubuntu/Debian:** `sudo apt install ffmpeg`
   - **MacOS:** `brew install ffmpeg`
   - **Windows:** Baixe e adicione ao PATH.

## ▶️ Uso

### Via Linha de Comando (CLI)

Converta um texto e toque o áudio imediatamente:
```bash
python src/tts_converter.py "Olá, isto é um teste."
```

Salvar com um nome específico e não tocar:
```bash
python src/tts_converter.py "Texto para salvar" -o meu_audio.mp3 --no-play
```

### Modo Interativo

Simplesmente execute o script sem argumentos:
```bash
python src/tts_converter.py
```
Você será solicitado a digitar a mensagem.

## 🧪 Testes

Para rodar os testes unitários:
```bash
python -m unittest discover tests
```

## 📝 Licença

Este projeto está licenciado sob a licença MIT - veja o arquivo [LICENSE](LICENSE) para mais detalhes.

## 👤 Autor

**Ítalo Felipe Lira de Morais**

---
