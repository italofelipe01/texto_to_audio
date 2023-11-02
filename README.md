<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Texto para Áudio em Português</title>
</head>
<body>
    <h1>Texto para Áudio em Português</h1>
    <p>Este é um programa simples que usa a biblioteca <a href="https://pypi.org/project/gTTS/" target="_blank">gTTS</a> para converter um texto em áudio em português. Ele também usa a biblioteca <a href="https://docs.python.org/3/library/os.html" target="_blank">os</a> para executar o comando <code>ffplay</code> para reproduzir o arquivo de áudio gerado.</p>
    <h2>Requisitos</h2>
    <ul>
        <li>Python 3.6 ou superior</li>
        <li>gTTS 2.2.3 ou superior</li>
        <li>ffmpeg 4.4 ou superior</li>
    </ul>
    <h2>Como usar</h2>
    <ol>
        <li>Clone este repositório ou baixe o arquivo <code>texto_para_audio.py</code></li>
        <li>Instale as dependências com o comando <code>pip install -r requirements.txt</code></li>
        <li>Execute o programa com o comando <code>python texto_para_audio.py</code></li>
        <li>Digite a sua mensagem a ser disponibilizada em áudio e pressione Enter</li>
        <li>Aguarde a geração do arquivo <code>audio.mp3</code> e a reprodução do mesmo</li>
    </ol>
    <h2>Exemplo</h2>
    <p>Insira sua mensagem a ser disponibilizada em áudio por favor: <em>Olá, este é um exemplo de texto para áudio em português.</em></p>
    <p>O programa irá gerar e reproduzir um arquivo de áudio com a seguinte mensagem:</p>
    <blockquote>
        Olá, este é um exemplo de texto para áudio em português.
    </blockquote>
    <h2>Licença</h2>
    <p>Este projeto está licenciado sob a licença MIT. Veja o arquivo <a href="URL_PARA_O_ARQUIVO_LICENSE" target="_blank">LICENSE</a> para mais detalhes.</p>
</body>
</html>