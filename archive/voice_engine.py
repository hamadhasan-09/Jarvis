"""
JARVIS Multi-Language Voice Engine v7.0
- Continuous live conversation mode
- Interrupt-on-speak detection
- Multi-language (English/Urdu/Hindi)
- Male/Female voice switching
"""

import os
import io
import wave
import asyncio
import threading
import tempfile
import ctypes
import re
import time
import numpy as np
import sounddevice as sd
import edge_tts
from groq import Groq
from dotenv import load_dotenv

load_dotenv()


class VoiceEngine:
    """Live voice engine with continuous conversation support."""

    VOICES = {
        "english_male": "en-US-GuyNeural",
        "english_female": "en-US-JennyNeural",
        "english_british": "en-GB-RyanNeural",
        "urdu_male": "ur-PK-AsadNeural",
        "urdu_female": "ur-PK-UzmaNeural",
        "hindi_male": "hi-IN-MadhurNeural",
        "hindi_female": "hi-IN-SwaraNeural",
    }

    def __init__(self):
        self.tts_available = True
        self.stt_available = False
        self.sample_rate = 16000
        self.groq_client = None

        self.current_voice = "english_male"
        self.is_speaking = False
        self.last_spoken_text = ""
        self._stop_event = threading.Event()
        self._live_mode = False
        self._live_thread = None

        print("  🔊 Live Voice Engine Online")

        try:
            groq_key = os.getenv("GROQ_API_KEY")
            if groq_key:
                self.groq_client = Groq(api_key=groq_key)
                sd.query_devices(kind='input')
                self.stt_available = True
                print("  🎤 Microphone Online (Live conversation ready)")
        except Exception:
            print("  ⚠️ Microphone standby")

    def set_voice(self, voice_key):
        if voice_key in self.VOICES:
            self.current_voice = voice_key
            return f"Voice: {voice_key}"
        return "Unknown voice"

    def toggle_gender(self):
        if self.current_voice.endswith("_male"):
            new_voice = self.current_voice[:-5] + "_female"
        elif self.current_voice.endswith("_female"):
            new_voice = self.current_voice[:-7] + "_male"
        else:
            return "Cannot toggle current voice"

        if new_voice in self.VOICES:
            self.current_voice = new_voice
            return f"Switched to: {self.current_voice}"
        return "Voice not available"

    def _get_voice_for_text(self, text):
        is_female = self.current_voice.endswith("_female")
        if re.search(r'[\u0600-\u06FF]', text):
            return self.VOICES["urdu_female" if is_female else "urdu_male"]
        if re.search(r'[\u0900-\u097F]', text):
            return self.VOICES["hindi_female" if is_female else "hindi_male"]
        return self.VOICES.get(self.current_voice, "en-US-GuyNeural")

    def stop_speaking(self):
        self._stop_event.set()
        try:
            winmm = ctypes.windll.winmm
            winmm.mciSendStringW('close all', None, 0, None)
        except Exception:
            pass
        self.is_speaking = False

    def _clean_text(self, text):
        if not text:
            return ""
        text = re.sub(r'[*_#`~]', '', text)
        text = re.sub(r'\[.*?\]\(.*?\)', '', text)
        text = re.sub(r'http\S+', '', text)
        return text.replace('"', '').replace("'", '').strip()

    async def _create_speech_file(self, text, voice_id, file_path):
        communicate = edge_tts.Communicate(text=text, voice=voice_id)
        await communicate.save(file_path)

    def speak(self, text, wait=False):
        """Speak text. If wait=True, block until finished."""
        if not text:
            return

        clean = self._clean_text(text)
        if not clean:
            return

        self.last_spoken_text = clean
        self.stop_speaking()
        self._stop_event.clear()

        voice_id = self._get_voice_for_text(clean)

        def _worker():
            self.is_speaking = True
            temp_path = None
            try:
                temp_dir = tempfile.gettempdir()
                temp_path = os.path.join(temp_dir, f"nexus_{threading.get_ident()}.mp3")

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._create_speech_file(clean, voice_id, temp_path))
                loop.close()

                if not self._stop_event.is_set() and os.path.exists(temp_path):
                    winmm = ctypes.windll.winmm
                    alias = f"spk_{threading.get_ident()}"
                    winmm.mciSendStringW(f'close {alias}', None, 0, None)
                    cmd_open = f'open "{temp_path}" type mpegvideo alias {alias}'
                    res = winmm.mciSendStringW(cmd_open, None, 0, None)
                    if res != 0:
                        cmd_open_plain = f'open "{temp_path}" alias {alias}'
                        winmm.mciSendStringW(cmd_open_plain, None, 0, None)
                    winmm.mciSendStringW(f'play {alias} wait', None, 0, None)
                    winmm.mciSendStringW(f'close {alias}', None, 0, None)
            except Exception as e:
                print(f"⚠️ Speech error: {e}")
            finally:
                self.is_speaking = False
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        if wait:
            t.join(timeout=30)

    def listen(self, max_duration=8, silence_threshold=500, silence_duration=1.2):
        """Single-shot listen with VAD."""
        if not self.stt_available or not self.groq_client:
            return ""

        self.stop_speaking()
        time.sleep(0.3)

        try:
            chunk_duration = 0.1
            chunk_frames = int(chunk_duration * self.sample_rate)
            max_chunks = int(max_duration / chunk_duration)
            silence_chunks_needed = int(silence_duration / chunk_duration)

            recording_chunks = []
            silence_counter = 0
            voice_started = False

            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype='int16',
                blocksize=chunk_frames
            )
            stream.start()

            for _ in range(max_chunks):
                chunk, _ = stream.read(chunk_frames)
                recording_chunks.append(chunk)
                volume = np.abs(chunk).mean()

                if volume > silence_threshold:
                    voice_started = True
                    silence_counter = 0
                elif voice_started:
                    silence_counter += 1
                    if silence_counter >= silence_chunks_needed:
                        break

            stream.stop()
            stream.close()

            if not voice_started:
                return ""

            recording = np.concatenate(recording_chunks, axis=0)
            return self._transcribe_audio(recording)

        except Exception as e:
            print(f"⚠️ Listen error: {e}")
            return ""

    def _transcribe_audio(self, recording):
        """Send audio buffer to Groq Whisper."""
        try:
            wav_io = io.BytesIO()
            with wave.open(wav_io, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                wf.writeframes(recording.tobytes())
            wav_io.seek(0)
            wav_io.name = "audio.wav"

            lang_hint = None
            if "urdu" in self.current_voice:
                lang_hint = "ur"
            elif "hindi" in self.current_voice:
                lang_hint = "hi"
            elif "english" in self.current_voice:
                lang_hint = "en"

            params = {
                "file": wav_io,
                "model": "whisper-large-v3-turbo",
                "response_format": "text"
            }
            if lang_hint:
                params["language"] = lang_hint

            transcription = self.groq_client.audio.transcriptions.create(**params)
            text = str(transcription).strip()

            if text and self.last_spoken_text and len(text) > 15:
                similarity = self._text_similarity(text.lower(), self.last_spoken_text.lower())
                if similarity > 0.7:
                    return ""

            return text
        except Exception as e:
            print(f"⚠️ Transcribe error: {e}")
            return ""

    def _text_similarity(self, a, b):
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return 0.0
        return len(words_a & words_b) / len(words_a | words_b)

    # ============================================
    # LIVE CONVERSATION MODE (NEW)
    # ============================================

    def start_live_conversation(self, on_user_speech, on_error=None):
        """
        Start continuous listening loop.
        Calls on_user_speech(text) whenever you say something.
        Automatically pauses while JARVIS is speaking.
        """
        if self._live_mode:
            return "Already in live mode"

        if not self.stt_available:
            return "Microphone not available"

        self._live_mode = True

        def _live_loop():
            print("🎙️ LIVE CONVERSATION MODE ACTIVE")
            silence_count = 0

            while self._live_mode:
                try:
                    # Don't listen while JARVIS is speaking (avoid echo)
                    if self.is_speaking:
                        time.sleep(0.2)
                        continue

                    # Short listen window
                    chunk_duration = 0.1
                    chunk_frames = int(chunk_duration * self.sample_rate)
                    max_chunks = 60  # 6 seconds max per utterance
                    silence_chunks_needed = 12  # 1.2s silence = end of utterance
                    volume_threshold = 500

                    recording_chunks = []
                    local_silence = 0
                    voice_started = False

                    stream = sd.InputStream(
                        samplerate=self.sample_rate,
                        channels=1,
                        dtype='int16',
                        blocksize=chunk_frames
                    )
                    stream.start()

                    for _ in range(max_chunks):
                        if not self._live_mode:
                            break

                        chunk, _ = stream.read(chunk_frames)
                        recording_chunks.append(chunk)
                        volume = np.abs(chunk).mean()

                        if volume > volume_threshold:
                            voice_started = True
                            local_silence = 0
                        elif voice_started:
                            local_silence += 1
                            if local_silence >= silence_chunks_needed:
                                break

                    stream.stop()
                    stream.close()

                    if not voice_started or not self._live_mode:
                        time.sleep(0.1)
                        continue

                    # We heard something — transcribe it
                    recording = np.concatenate(recording_chunks, axis=0)
                    text = self._transcribe_audio(recording)

                    if text and len(text) > 2:
                        print(f"👤 (Live): {text}")
                        try:
                            on_user_speech(text)
                        except Exception as e:
                            if on_error:
                                on_error(e)

                except Exception as e:
                    print(f"⚠️ Live loop error: {e}")
                    if on_error:
                        on_error(e)
                    time.sleep(1)

            print("🎙️ Live conversation ended")

        self._live_thread = threading.Thread(target=_live_loop, daemon=True)
        self._live_thread.start()
        return "Live conversation started"

    def stop_live_conversation(self):
        """Stop the continuous listening loop."""
        self._live_mode = False
        self.stop_speaking()
        return "Live conversation stopped"

    def is_live_active(self):
        return self._live_mode