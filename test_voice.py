import os
import asyncio
import tempfile
import ctypes
import edge_tts

# Windows Native Multimedia Player (Built into every Windows PC)
def play_audio(file_path):
    winmm = ctypes.windll.winmm
    alias = "jarvis_snd"
    winmm.mciSendStringW(f'close {alias}', None, 0, None)
    res_open = winmm.mciSendStringW(f'open "{file_path}" type mpegvideo alias {alias}', None, 0, None)
    if res_open != 0:
        # Fallback without type specifier
        winmm.mciSendStringW(f'open "{file_path}" alias {alias}', None, 0, None)
    winmm.mciSendStringW(f'play {alias} wait', None, 0, None)
    winmm.mciSendStringW(f'close {alias}', None, 0, None)

async def speak_test(text, voice_id, label):
    print(f"\n▶ Playing: {label}...")
    temp_file = os.path.join(tempfile.gettempdir(), f"test_{voice_id}.mp3")
    comm = edge_tts.Communicate(text, voice_id)
    await comm.save(temp_file)
    play_audio(temp_file)
    if os.path.exists(temp_file):
        try:
            os.remove(temp_file)
        except Exception:
            pass

async def main():
    print("========================================")
    print("  Testing JARVIS High-Definition Voices")
    print("========================================")

    # 1. English Male (Deep & Natural)
    await speak_test(
        "Hello Sir, I am Jarvis. My new neural voice engine is fully active.",
        "en-US-GuyNeural",
        "1. English Male (US Guy)"
    )

    # 2. English Female (Clear & Smooth)
    await speak_test(
        "And this is my female voice. Crisp, natural, and clear.",
        "en-US-JennyNeural",
        "2. English Female (US Jenny)"
    )

    # 3. Urdu Male (Pakistan)
    await speak_test(
        "السلام علیکم سر، میں آپ کا اسسٹنٹ جارویس ہوں۔ میں اردو روانی سے بول سکتا ہوں۔",
        "ur-PK-AsadNeural",
        "3. Urdu Male (Asad Pakistan)"
    )

    # 4. Hindi Female (India)
    await speak_test(
        "नमस्ते, मैं आपकी क्या मदद कर सकती हूँ?",
        "hi-IN-SwaraNeural",
        "4. Hindi Female (Swara India)"
    )

    print("\n✅ All 4 voice tests completed successfully!")

if __name__ == "__main__":
    asyncio.run(main())