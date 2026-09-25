import os
from dotenv import load_dotenv
from groq import Groq
from core.prompt_kernel import CORE_SYSTEM_PROMPT
from core.voice_engine import speak
from core.listener import listen_and_transcribe

load_dotenv()

def run_jarvis():
    print("==========================================")
    print("  JARVIS Voice Assistant - Full Cycle Test")
    print("==========================================")
    
    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    except Exception as e:
        print(f"Connection error: {e}")
        return

    speak("Jarvis is online and listening, sir.")
    print("\nJARVIS is ready. Press Enter to speak, or type 'exit' to quit.\n")
    
    while True:
        user_choice = input("\n[Press Enter to Speak | Type 'q' to Quit]: ")
        if user_choice.lower() in ['q', 'exit', 'quit']:
            break
            
        user_text = listen_and_transcribe(client, duration=5)
        
        if not user_text:
            print("JARVIS: I didn't catch that, sir.")
            continue
            
        print(f"You (Heard): {user_text}")
        
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[
                    {"role": "system", "content": CORE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_text}
                ],
                max_tokens=150,
                temperature=0.5
            )
            
            reply = response.choices[0].message.content
            print(f"\nJARVIS: {reply}\n")
            speak(reply)
            
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    run_jarvis()