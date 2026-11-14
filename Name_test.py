"""Finds the best spelling for how Remielle says your name.

1. Start the GPT-SoVITS voice server first (Launch_Remielle.bat does it, or Start_Voice_Server.bat).
2. Run:  python name_test.py
3. Listen to each numbered version (it plays through your speakers), then put the winner in .env:
       REMIELLE_SPOKEN_NAME=<the spelling you liked>

Want to try your own spellings?  python name_test.py "Fuh zeel" "Phazeel"
"""
import os
import sys
import time

import voice_tts

# The first four are made of real English words (reliable). The rest are invented spellings, for comparison.
CANDIDATES = ["Fa zeal", "Fah zeal", "Fuzz ill", "Fa zill", "Fa zeel", "Fazeel", "Fazil", "Fah zil"]
SENTENCE = "[happy] Hello {name}, I missed you so much today."


def play(path: str) -> None:
    try:
        import winsound   # built into Windows: plays the wav and waits until it finishes
        winsound.PlaySound(path, winsound.SND_FILENAME)
    except Exception:
        print(f"      (could not play it automatically - open {path} yourself)")


def main() -> int:
    names = sys.argv[1:] or CANDIDATES
    os.makedirs("static", exist_ok=True)
    made = []
    for i, name in enumerate(names, 1):
        print(f"\n[{i}/{len(names)}] Generating: {name!r}")
        path = voice_tts.play_anime_voice(SENTENCE.format(name=name), os.path.join("static", f"name_test_{i}.wav"))
        if not path:
            print("      Failed - is the GPT-SoVITS server running? (see the message above)")
            return 1
        print(f"      Playing version {i}: {name!r}")
        play(path)
        made.append((i, name))
        time.sleep(0.4)

    print("\n=== Done ===")
    for i, name in made:
        print(f"  {i}. {name}")
    print("\nPut your favourite in .env, for example:")
    print(f"  REMIELLE_SPOKEN_NAME={made[0][1]}")
    print("Replay one any time: static\\name_test_<number>.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())