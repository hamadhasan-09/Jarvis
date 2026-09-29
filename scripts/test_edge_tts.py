"""Generate English and Urdu Edge TTS MP3 files without playing them."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from voice.tts import EdgeTTS


def main() -> None:
    engine = EdgeTTS()
    outputs = (
        ("Hello, this is a short English voice test.", PROJECT_ROOT / "test_en.mp3"),
        ("السلام علیکم، یہ اردو آواز کا مختصر ٹیسٹ ہے۔", PROJECT_ROOT / "test_ur.mp3"),
    )
    for sentence, output_path in outputs:
        if not engine.save_mp3(sentence, output_path):
            raise RuntimeError(f"Edge TTS could not generate {output_path}")
        print(output_path)


if __name__ == "__main__":
    main()
