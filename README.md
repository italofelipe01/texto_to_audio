# Texto para Áudio (TTS Converter)

![Version](https://img.shields.io/badge/version-v1.0.0-blue?style=for-the-badge)
![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)
![Status](https://img.shields.io/badge/status-stable-success?style=for-the-badge)
![Python](https://img.shields.io/badge/python-3.6%2B-blueviolet?style=for-the-badge)

## 📄 Descrição
O **Texto para Áudio** é uma solução de software desenvolvida para converter strings de texto em arquivos de áudio falado (Text-to-Speech) utilizando a API do Google Translate (gTTS). O sistema automatiza a síntese de voz em Português Brasileiro e a reprodução imediata do conteúdo gerado, servindo como ferramenta de apoio à acessibilidade e automação de mídia.

## 🚀 Funcionalidades
- **Síntese de Voz:** Conversão de texto livre para áudio em Português (`pt-br`).
- **Reprodução Automática:** Integração nativa com `ffplay` (FFmpeg) para execução imediata do áudio gerado.
- **Exportação de Arquivo:** Gera e salva automaticamente o arquivo `audio.mp3` no diretório local.
- **Interface de Linha de Comando (CLI):** Interação simples e direta via terminal.

## 🛠 Tecnologias Utilizadas
- **Linguagem:** Python 3.6 ou superior.
- **Bibliotecas:**
  - `gTTS` (Google Text-to-Speech) v2.2.3+.
  - `os` (Interação com Sistema Operacional).
  - `datetime` (Gestão de Timestamp).
- **Dependência de Sistema:** FFmpeg 4.4 ou superior (necessário para o comando `ffplay`).

## 📦 Instalação

1. **Clone o repositório:**
```bash
git clone https://github.com/italofelipe01/texto_to_audio.git
```

2. **Instale as dependências do Python:**
```bash
pip install -r requirements.txt
```

3. **Instale o FFmpeg (Requisito de Sistema):**
   - Certifique-se de que o `ffmpeg` está instalado e acessível no PATH do sistema, pois o script utiliza o comando `ffplay`.

## ▶️ Uso

1. Execute o script principal via terminal:
```bash
python texto_to_audio.py
```

2. Quando solicitado, digite a mensagem que deseja converter:
```
Insira sua mensagem a ser disponibilizada em audio por favor: Olá, este é um teste.
```

3. O sistema irá gerar o arquivo `audio.mp3` e reproduzi-lo automaticamente.

## 📝 Licença

Este projeto está licenciado sob a licença MIT - veja o arquivo [LICENSE](LICENSE) para mais detalhes.

## 👤 Autor

**Ítalo Felipe Lira de Morais**

---
