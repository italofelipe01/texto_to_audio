import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import subprocess

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from tts_converter import TTSConverter

class TestTTSConverter(unittest.TestCase):

    @patch('tts_converter.gTTS')
    def test_convert_to_audio(self, mock_gtts):
        # Setup mock
        mock_tts_instance = MagicMock()
        mock_gtts.return_value = mock_tts_instance

        converter = TTSConverter()
        output_file = 'test_output.mp3'
        text = 'Hello Test'

        # Execute
        result = converter.convert_to_audio(text, output_file)

        # Verify
        mock_gtts.assert_called_with(text, lang='pt', tld='com.br')
        mock_tts_instance.save.assert_called_with(output_file)
        self.assertEqual(result, output_file)

    @patch('shutil.which')
    @patch('subprocess.run')
    def test_play_audio(self, mock_subprocess_run, mock_shutil_which):
        # Create a dummy file to test existence check
        with open('dummy.mp3', 'w') as f:
            f.write('dummy')

        converter = TTSConverter()
        mock_shutil_which.return_value = '/usr/bin/ffplay'
        mock_subprocess_run.return_value = MagicMock(returncode=0)

        converter.play_audio('dummy.mp3')

        # Verify call to ffplay
        mock_subprocess_run.assert_called_with(
            ['ffplay', '-autoexit', '-nodisp', '-loglevel', 'quiet', 'dummy.mp3'],
            check=False
        )

        # Clean up
        os.remove('dummy.mp3')

    def test_play_audio_file_not_found(self):
        converter = TTSConverter()
        with self.assertLogs(level='ERROR') as cm:
            converter.play_audio('non_existent_file.mp3')
            self.assertTrue(any('File not found' in o for o in cm.output))

    @patch('shutil.which')
    def test_play_audio_ffplay_not_found(self, mock_shutil_which):
        # Create a dummy file
        with open('dummy.mp3', 'w') as f:
            f.write('dummy')

        mock_shutil_which.return_value = None
        converter = TTSConverter()

        with self.assertLogs(level='ERROR') as cm:
            converter.play_audio('dummy.mp3')
            self.assertTrue(any('ffplay is not installed' in o for o in cm.output))

        os.remove('dummy.mp3')

if __name__ == '__main__':
    unittest.main()
