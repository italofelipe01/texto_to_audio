from gtts import gTTS
import os
from datetime import datetime

time = datetime.now
text = input("Insira sua mensagem a ser disponibilizada em audio por favor: ")
tts = gTTS(text, lang='pt', tld='com.br')
tts.save('audio.mp3')

os.system("ffplay -autoexit audio.mp3")