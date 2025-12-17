import argparse
import logging
import os
import sys
import subprocess
import shutil
from gtts import gTTS # type: ignore

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TTSConverter:
    """
    A class to convert text to speech using Google Text-to-Speech (gTTS).
    """

    def __init__(self, lang: str = 'pt', tld: str = 'com.br'):
        """
        Initialize the TTSConverter.

        Args:
            lang (str): The language to use (default: 'pt').
            tld (str): The top-level domain for the accent (default: 'com.br').
        """
        self.lang = lang
        self.tld = tld

    def convert_to_audio(self, text: str, output_file: str = 'audio.mp3') -> str:
        """
        Converts the given text to an audio file.

        Args:
            text (str): The text to convert.
            output_file (str): The path to save the audio file.

        Returns:
            str: The path to the saved audio file.
        """
        try:
            logger.info(f"Converting text: '{text[:30]}...' to audio.")
            tts = gTTS(text, lang=self.lang, tld=self.tld)
            tts.save(output_file)
            logger.info(f"Audio saved successfully to: {output_file}")
            return output_file
        except Exception as e:
            logger.error(f"Error converting text to audio: {e}")
            raise

    def play_audio(self, file_path: str):
        """
        Plays the audio file using ffplay.

        Args:
            file_path (str): The path to the audio file.
        """
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return

        # Check if ffplay is installed
        if not shutil.which("ffplay"):
            logger.error("ffplay is not installed or not found in PATH. Please install FFmpeg.")
            return

        logger.info(f"Playing audio: {file_path}")
        try:
            # Use subprocess.run for security instead of os.system
            # -autoexit: Close after playing
            # -nodisp: Do not display graphical window
            # -loglevel quiet: Suppress ffplay output
            result = subprocess.run(
                ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", file_path],
                check=False
            )

            if result.returncode != 0:
                 logger.warning(f"ffplay returned a non-zero exit code: {result.returncode}")
        except Exception as e:
            logger.error(f"Error playing audio: {e}")

def main():
    parser = argparse.ArgumentParser(description="Convert text to speech (TTS) and play it.")
    parser.add_argument("text", nargs="?", help="The text to convert to audio.")
    parser.add_argument("-o", "--output", default="audio.mp3", help="Output filename (default: audio.mp3)")
    parser.add_argument("--no-play", action="store_true", help="Do not play the audio after generating.")

    args = parser.parse_args()

    converter = TTSConverter()

    text = args.text
    if not text:
        # Interactive mode if no text argument provided
        try:
            text = input("Insira sua mensagem a ser disponibilizada em audio: ").strip()
            if not text:
                logger.error("No text provided. Exiting.")
                return
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
            return

    try:
        output_file = converter.convert_to_audio(text, args.output)

        if not args.no_play:
            converter.play_audio(output_file)

    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
