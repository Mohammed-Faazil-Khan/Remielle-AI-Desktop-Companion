import speech_recognition as sr
import requests
from agent_brain import chat_with_agent

STATE_URL = "http://127.0.0.1:8000/set_state/"

def update_avatar_state(state: str):
    """Sends a signal to the 3D avatar to change its expression/animation."""
    try:
        requests.get(f"{STATE_URL}{state}", timeout=2)
    except requests.exceptions.RequestException:
        pass

def listen_for_commands():
    recognizer = sr.Recognizer()
    microphone = sr.Microphone()

    with microphone as source:
        print("🎙️ Adjusting to ambient noise...")
        recognizer.adjust_for_ambient_noise(source, duration=5)
        print("✅ Ready to listen!")

    while True:
        try:
            with microphone as source:
                update_avatar_state("idle")
                print("\n👂 Listening...")
                update_avatar_state("listening")

                audio = recognizer.listen(source, timeout=5, phrase_time_limit=15)
                update_avatar_state("thinking")
                print("🧠 Transcribing...")

                text = recognizer.recognize_google(audio)
                print(f"🗣️ You said: {text}")

                if text.strip():
                    chat_with_agent(text)

        except sr.WaitTimeoutError:
            continue
        except sr.UnknownValueError:
            print("🤷 Could not understand audio")
        except sr.RequestError as e:
            print(f"❌ Could not request results; {e}")
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    listen_for_commands()