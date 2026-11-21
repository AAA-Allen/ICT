"""Huawei SIS TTS adapter. Credentials stay on the server."""
import base64
import binascii
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class SpeechError(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SpeechSynthesizer:
    def __init__(self):
        self.region = os.getenv("FITNESS_SIS_REGION", "cn-north-4")
        self.project = os.getenv("FITNESS_SIS_PROJECT_ID", "")
        self.voice = os.getenv("FITNESS_SIS_VOICE", "chinese_xiaoyu_common")
        if not re.fullmatch(r"[a-z]+-[a-z]+-\d+", self.region):
            raise ValueError("invalid SIS region")
        if self.project and not re.fullmatch(r"[A-Za-z0-9_-]+", self.project):
            raise ValueError("invalid SIS project id")

    @property
    def configured(self):
        return bool(self.project and os.getenv("FITNESS_SIS_TOKEN"))

    def synthesize(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 120:
            raise SpeechError("SPEECH_INVALID_TEXT")
        if not self.configured:
            raise SpeechError("SPEECH_NOT_CONFIGURED")
        body = {"text": text.strip(), "config": {
            "audio_format": "mp3", "sample_rate": "16000", "property": self.voice,
            "speed": 0, "volume": 70,
        }}
        request = Request(
            f"https://sis-ext.{self.region}.myhuaweicloud.com/v1/{self.project}/tts",
            data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "X-Auth-Token": os.environ["FITNESS_SIS_TOKEN"]},
        )
        try:
            with build_opener(_NoRedirect()).open(request, timeout=8) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise SpeechError("SPEECH_INVALID_RESPONSE")
            result = json.loads(raw)
            data = result["result"]["data"]
            if not isinstance(data, str) or not base64.b64decode(data, validate=True):
                raise SpeechError("SPEECH_INVALID_RESPONSE")
            return {"audio_base64": data, "mime_type": "audio/mpeg", "provider": "huawei-sis"}
        except HTTPError as exc:
            raise SpeechError("SPEECH_AUTH_FAILED" if exc.code in (401, 403)
                              else "SPEECH_PROVIDER_FAILED") from None
        except (URLError, TimeoutError, OSError):
            raise SpeechError("SPEECH_UNAVAILABLE") from None
        except (ValueError, KeyError, TypeError, binascii.Error):
            raise SpeechError("SPEECH_INVALID_RESPONSE") from None
