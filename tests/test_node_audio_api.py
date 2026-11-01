import io
import wave
import unittest
from tests.test_asset_api import AssetApiTests


def wav_bytes():
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(8000); output.writeframes(b'\x00\x00' * 8000)
    return stream.getvalue()


class NodeAudioApiTests(unittest.TestCase):
    setUp = AssetApiTests.setUp
    tearDown = AssetApiTests.tearDown

    def test_audio_is_original_durable_asset_deduplicated_not_an_image_or_task(self):
        data = wav_bytes()
        first = self.client.post('/api/assets/import-audio', files={'file': ('音频.wav', data, 'audio/wav')})
        self.assertEqual(first.status_code, 200, first.text)
        asset = first.json(); self.assertEqual(asset['kind'], 'audio'); self.assertEqual(asset['duration_seconds'], 1)
        replay = self.client.post('/api/assets/import-audio', files={'file': ('另一名称.wav', data, 'audio/wav')})
        self.assertEqual(replay.json()['id'], asset['id'])
        content = self.client.get(f"/api/assets/{asset['id']}/content")
        self.assertEqual(content.content, data)
        thumb = self.client.get(f"/api/assets/{asset['id']}/thumbnail"); self.assertEqual(thumb.status_code, 200)
        self.assertEqual(self.ledger.list_jobs(), [])
        invalid = self.client.post('/api/assets/import-audio', files={'file': ('fake.mp3', b'not audio', 'audio/mpeg')})
        self.assertEqual(invalid.status_code, 400)
