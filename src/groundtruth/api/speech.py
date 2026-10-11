"""Neural text to speech for the spoken commentary, from Azure AI Speech, with no keys.

The app's managed identity gets an Entra token for the Foundry account (which also
serves Speech) and calls the regional text-to-speech endpoint. Each language has an
analyst voice and a fan voice, so the two viewer modes sound like two people.

GROUNDTRUTH_SPEECH_REGION        the account's region, e.g. swedencentral
GROUNDTRUTH_SPEECH_RESOURCE_ID   the account's resource id (part of the Entra auth header)
GROUNDTRUTH_TTS_CHARS_PER_HOUR   spoken characters allowed per rolling hour (default 60,000)
"""

from __future__ import annotations

import os
import time
from collections import deque
from xml.sax.saxutils import escape

VOICES = {
    ("en", "analyst"): "en-GB-RyanNeural", ("en", "casual"): "en-GB-SoniaNeural",
    ("es", "analyst"): "es-ES-AlvaroNeural", ("es", "casual"): "es-ES-ElviraNeural",
    ("ar", "analyst"): "ar-SA-HamedNeural", ("ar", "casual"): "ar-SA-ZariyahNeural",
}
LOCALE = {"en": "en-GB", "es": "es-ES", "ar": "ar-SA"}


class Speech:
    def __init__(self, region: str, resource_id: str, chars_per_hour: int = 60_000):
        self.region, self.resource_id, self.chars_per_hour = region, resource_id, chars_per_hour
        self._token: tuple[str, float] | None = None
        self._spent: deque[tuple[float, int]] = deque()
        self._cred = None

    @classmethod
    def from_env(cls) -> Speech | None:
        region, rid = os.environ.get("GROUNDTRUTH_SPEECH_REGION"), os.environ.get("GROUNDTRUTH_SPEECH_RESOURCE_ID")
        if not region or not rid:
            return None
        return cls(region, rid, int(os.environ.get("GROUNDTRUTH_TTS_CHARS_PER_HOUR", "60000")))

    def _bearer(self) -> str:
        if self._token is None or self._token[1] - time.time() < 120:
            from azure.identity import DefaultAzureCredential

            self._cred = self._cred or DefaultAzureCredential()
            tok = self._cred.get_token("https://cognitiveservices.azure.com/.default")
            self._token = (tok.token, float(tok.expires_on))
        return f"Bearer aad#{self.resource_id}#{self._token[0]}"

    def allow(self, n: int) -> bool:
        now = time.time()
        while self._spent and now - self._spent[0][0] > 3600:
            self._spent.popleft()
        if sum(c for _, c in self._spent) + n > self.chars_per_hour:
            return False
        self._spent.append((now, n))
        return True

    async def synthesize(self, text: str, lang: str, mode: str) -> bytes:
        import httpx

        voice = VOICES.get((lang, mode), VOICES[("en", "analyst")])
        ssml = (f"<speak version='1.0' xml:lang='{LOCALE.get(lang, 'en-GB')}'><voice name='{voice}'>"
                f"<prosody rate='+6%'>{escape(text)}</prosody></voice></speak>")
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1",
                content=ssml.encode("utf-8"),
                headers={"Authorization": self._bearer(), "Content-Type": "application/ssml+xml",
                         "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3", "User-Agent": "groundtruth"})
            r.raise_for_status()
            return r.content
