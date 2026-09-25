import os
from dotenv import load_dotenv
from groq import Groq
from core.prompt_kernel import CORE_SYSTEM_PROMPT

load_dotenv()

def test_jarvis_brain():
    print("Initializing JARVIS Core...")
    
    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    except Exception as e:
        print(f"Failed to connect. Check your .env file: {e}")
        return

    print("\nSystem Online. Type 'exit' to quit.")
    print("-" * 40)
    
    while True:
        user_input = input("\nYou: ")
        if user_input.lower() in ['exit', 'quit']:
            break
            
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[
                    {"role": "system", "content": CORE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_input}
                ],
                max_tokens=150,
                temperature=0.5
            )
            
            reply = response.choices[0].message.content
            print(f"\nJARVIS: {reply}")
            
        except Exception as e:
            print(f"\nAPI Error: {e}")

if __name__ == "__main__":
    test_jarvis_brain()