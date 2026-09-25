import os
import io
import wave
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION = 0.1  # 100ms chunks
SILENCE_LIMIT = 0.6   # 600ms silence cutoff

def get_rms(data):
    """Calculates RMS audio energy."""
    return np.sqrt(np.mean(np.square(data)))

def record_dynamic_audio():
    """Captures microphone input with sensitive VAD and safety timeouts."""
    chunk_samples = int(SAMPLE_RATE * CHUNK_DURATION)
    max_silent_chunks = int(SILENCE_LIMIT / CHUNK_DURATION)
    
    audio_buffer = []
    is_recording = False
    silent_chunks = 0

    try:
        # Bind to active Windows default recording device
        default_mic = sd.default.device[0]
        
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32', device=default_mic) as stream:
            # 0.3s quick room noise calibration
            calibration_volumes = []
            for _ in range(int(0.3 / CHUNK_DURATION)):
                chunk, _ = stream.read(chunk_samples)
                calibration_volumes.append(get_rms(chunk))
            
            ambient = np.mean(calibration_volumes) if calibration_volumes else 0.001
            # Dynamic sensitivity floor
            dynamic_threshold = max(ambient * 1.2, 0.003)
            
            total_listened_chunks = 0
            max_listen_chunks = int(4.0 / CHUNK_DURATION) # 4s max idle wait per turn

            while total_listened_chunks < max_listen_chunks:
                chunk, _ = stream.read(chunk_samples)
                volume = get_rms(chunk)
                total_listened_chunks += 1

                if volume > dynamic_threshold:
                    if not is_recording:
                        print(f"[AUDIO] Speech detected! (Vol: {volume:.4f} > Thresh: {dynamic_threshold:.4f})")
                        is_recording = True
                    silent_chunks = 0
                    audio_buffer.append(chunk)
                
                elif is_recording:
                    silent_chunks += 1
                    audio_buffer.append(chunk)
                    
                    if silent_chunks > max_silent_chunks:
                        print("[AUDIO] End of speech detected.")
                        break

    except Exception as e:
        print(f"[Mic Error]: {e}")
        return None

    if not audio_buffer:
        return None

    # Convert recorded audio directly into RAM WAV buffer
    audio_data = np.concatenate(audio_buffer, axis=0)
    audio_data_int16 = np.int16(np.clip(audio_data, -1.0, 1.0) * 32767)

    wav_io = io.BytesIO()
    wav_io.name = "speech.wav"
    
    with wave.open(wav_io, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_data_int16.tobytes())
        
    wav_io.seek(0)
    return wav_io

def listen_and_transcribe(groq_client, duration=None):
    """Captures RAM audio buffer and sends to Groq Whisper Turbo."""
    wav_buffer = record_dynamic_audio()
    
    if not wav_buffer:
        return ""
    
    try:
        transcription = groq_client.audio.transcriptions.create(
            file=(wav_buffer.name, wav_buffer.read()),
            model="whisper-large-v3-turbo",
            prompt="Hello JARVIS. تم کیسے ہو؟ The user speaks English and Urdu.",
            response_format="text",
            temperature=0.0
        )
        return str(transcription).strip()
    except Exception as e:
        print(f"[STT Error]: {e}")
        return ""