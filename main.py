import requests
import atexit
import subprocess
import sys
import time
from ears_stt import listen_and_transcribe
from agent_brain import chat_with_agent

STATE_URL = "http://127.0.0.1:8000/set_state/"
app_process = None


def update_avatar_state(state: str, text: str = ""):
    """Sends a state update (and, for 'speaking', the text to say) to the avatar window."""
    try:
        params = {"text": text} if text else {}
        requests.get(f"{STATE_URL}{state}", params=params, timeout=1)
    except requests.exceptions.ConnectionError:
        pass


def cleanup():
    """Failsafe: Forces avatar back to idle and closes the UI if you stop the script."""
    update_avatar_state("idle")
    if app_process:
        print("\n🛑 Closing Avatar Window...")
        app_process.terminate()


atexit.register(cleanup)

if __name__ == "__main__":
    print("🖥️ Launching Avatar Engine...")

    # 1. Automatically launch app.py as a background process
    app_process = subprocess.Popen([sys.executable, "app.py"])

    # Give the FastAPI server and Three.js engine 4 seconds to fully render
    time.sleep(4)

    print("🚀 Anime AI Desktop Assistant is fully online!")

    # 2. Trigger startup intro *once* on boot
    update_avatar_state("thinking")
    time.sleep(1)

    intro_text = "Hello! I am Remielle, your local AI assistant. How can I help you today?"
    print(f"🤖 Assistant: {intro_text}")

    update_avatar_state("speaking", text=intro_text)

    # Let her finish speaking her intro completely before starting the mic
    time.sleep(4.5)
    update_avatar_state("idle")
    time.sleep(0.5)

    # 3. Enter permanent voice-listening loop
    while True:
        try:
            update_avatar_state("listening")
            user_command = listen_and_transcribe()

            if user_command:
                print(f"\n👤 You said: {user_command}")

                # Avatar thinks while processing the prompt
                update_avatar_state("thinking")
                assistant_response = chat_with_agent(user_command)

                print(f"🤖 Assistant: {assistant_response}")

                # Avatar speaks the response — the frontend generates and
                # plays the audio itself from this text, so it's the actual
                # response every time (not a placeholder).
                update_avatar_state("speaking", text=assistant_response)

                # Buffer to prevent mic from hearing the end of her sentence
                time.sleep(1.0)

                # Return to idle before listening again
                update_avatar_state("idle")
                time.sleep(0.5)

        except KeyboardInterrupt:
            print("\n🛑 Shutting down assistant. See ya!")
            break
        except Exception as e:
            print(f"❌ Error in execution loop: {e}")
            update_avatar_state("idle")
            time.sleep(1)