import base64
import io
import json
import os
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

from fitness_backend.speech import SpeechSynthesizer, SpeechError


class SpeechTests(unittest.TestCase):
    def test_not_configured_and_bad_text_do_not_call_cloud(self):
        with patch.dict(os.environ, {}, clear=True), patch('fitness_backend.speech.build_opener') as opener:
            service = SpeechSynthesizer()
            self.assertFalse(service.configured)
            for text in (None, '', ' ' * 10, '字' * 121):
                with self.assertRaisesRegex(SpeechError, 'SPEECH_INVALID_TEXT'):
                    service.synthesize(text)
            with self.assertRaisesRegex(SpeechError, 'SPEECH_NOT_CONFIGURED'):
                service.synthesize('请全身入镜')
            opener.assert_not_called()

    def test_provider_contract_and_response_validation(self):
        with patch.dict(os.environ, {'FITNESS_SIS_PROJECT_ID': 'project', 'FITNESS_SIS_TOKEN': 'secret'}, clear=True), patch('fitness_backend.speech.build_opener') as opener:
            service = SpeechSynthesizer()
            for response, expected in [
                ({'result': {'data': base64.b64encode(b'audio').decode()}}, None),
                ({'result': {'data': '!bad'}}, 'SPEECH_INVALID_RESPONSE'),
                ({'error_code': 'failed'}, 'SPEECH_INVALID_RESPONSE'),
                ([], 'SPEECH_INVALID_RESPONSE'),
            ]:
                opener.return_value.open.return_value.__enter__.return_value = io.BytesIO(json.dumps(response).encode())
                if expected:
                    with self.assertRaisesRegex(SpeechError, expected):
                        service.synthesize('请全身入镜')
                else:
                    self.assertEqual(service.synthesize('请全身入镜')['mime_type'], 'audio/mpeg')
            request = opener.return_value.open.call_args.args[0]
            self.assertIn('/v1/project/tts', request.full_url)
            self.assertEqual(json.loads(request.data)['config']['audio_format'], 'mp3')
            opener.return_value.open.side_effect = HTTPError(request.full_url, 401, 'secret', {}, None)
            with self.assertRaisesRegex(SpeechError, '^SPEECH_AUTH_FAILED$'):
                service.synthesize('请全身入镜')
