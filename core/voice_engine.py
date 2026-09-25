import os
import asyncio
import tempfile
import ctypes
import edge_tts

DEFAULT_VOICE = "en-US-GuyNeural"

def play_audio(file_path):
    """Plays MP3 audio using Windows Native MCI (winmm.dll)."""
    winmm = ctypes.windll.winmm
    alias = "jarvis_snd"
    winmm.mciSendStringW(f'close {alias}', None, 0, None)
    res_open = winmm.mciSendStringW(f'open "{file_path}" type mpegvideo alias {alias}', None, 0, None)
    if res_open != 0:
        winmm.mciSendStringW(f'open "{file_path}" alias {alias}', None, 0, None)
    winmm.mciSendStringW(f'play {alias} wait', None, 0, None)
    winmm.mciSendStringW(f'close {alias}', None, 0, None)

async def _generate_and_play(text, voice_id):
    temp_file = os.path.join(tempfile.gettempdir(), "jarvis_temp_speech.mp3")
    comm = edge_tts.Communicate(text, voice_id)
    await comm.save(temp_file)
    play_audio(temp_file)
    
    if os.path.exists(temp_file):
        try:
            os.remove(temp_file)
        except Exception:
            pass

def speak(text, voice_id=DEFAULT_VOICE):
    if not text or not text.strip():
        return
    try:
        asyncio.run(_generate_and_play(text, voice_id))
    except Exception as e:
        print(f"[Voice Output Error]: {e}")