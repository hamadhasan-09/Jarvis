"""Exercise ElevenLabs Urdu routing with a mocked HTTP response, never network."""

from pathlib import Path
import sys
from unittest.mock import Mock, patch

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from voice.config import load_config
from voice.tts import EdgeTTS, ElevenLabsTTS


class _TestKeyManager:
    def get_key(self, service: str) -> str | None:
        assert service == "elevenlabs"
        return "mock-key-for-request-test"


def main() -> None:
    config = load_config()
    response = Mock()
    response.content = b"mocked-elevenlabs-audio-response"
    response.raise_for_status.return_value = None
    client = Mock()
    client.post.return_value = response

    elevenlabs = ElevenLabsTTS(config, key_manager=_TestKeyManager(), client=client)
    routed = EdgeTTS(config, elevenlabs=elevenlabs)
    output_path = PROJECT_ROOT / "test_ur_elevenlabs.mp3"
    sentence = "السلام علیکم، یہ اردو آواز کا ٹیسٹ ہے۔"

    assert routed.save_mp3(sentence, output_path)
    with patch("voice.tts._decode_mp3", return_value=np.ones(16, dtype=np.float32)):
        pcm = routed.synthesize(sentence, "ur")
    assert pcm.size == 16

    expected_url = elevenlabs.ENDPOINT.format(config.section("tts")["elevenlabs"]["voice_id"])
    assert client.post.call_count == 2
    for call in client.post.call_args_list:
        args, kwargs = call
        assert args[0] == expected_url
        assert kwargs["json"] == {
            "text": sentence,
            "model_id": config.section("tts")["elevenlabs"]["model_id"],
        }
        assert kwargs["headers"]["xi-api-key"] == "mock-key-for-request-test"
    assert output_path.is_file() and output_path.stat().st_size > 0
    print(f"Mocked Urdu ElevenLabs request verified; response bytes saved to {output_path}")


if __name__ == "__main__":
    main()
