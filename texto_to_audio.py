from gtts import gTTS

text = input("Insira sua mensagem a ser disponibilizada em audio por favor: ")
tts = gTTS(text, lang='pt', tld='com.br')
tts.save('audio.mp3')