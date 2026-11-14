"""Run this ONCE to link Remielle to your Spotify account.

1. Go to developer.spotify.com > Dashboard > your app, and copy the Client ID and Client Secret.
2. In that app's settings add this Redirect URI exactly:  http://127.0.0.1:8888/callback
3. Put the two values in the ".env" file next to this script (copy ".env.example" to ".env"):
       SPOTIPY_CLIENT_ID=...
       SPOTIPY_CLIENT_SECRET=...
   Never write the secret inside a .py file and never commit .env to GitHub.
4. Run:  python spotify_login.py
   A browser opens, you approve, and the login is saved to ".spotify_cache" in this folder.
"""
import sys

import remielle_config as cfg


def main() -> int:
    missing = cfg.spotify_credentials_missing()
    if missing:
        print("❌ Missing: " + ", ".join(missing))
        print("   Add them to the .env file next to this script (see the instructions at the top of this file).")
        return 1
    try:
        import spotipy
    except ImportError:
        print("❌ The spotipy package isn't installed. Run:  pip install spotipy")
        return 1

    print(f"Redirect URI in use: {cfg.SPOTIFY_REDIRECT_URI}")
    print("   (it must be listed in your Spotify app's settings, character for character)")
    auth = cfg.spotify_oauth(open_browser=True)
    try:
        try:
            auth.get_access_token(as_dict=False)
        except TypeError:   # newer spotipy versions dropped the as_dict argument
            auth.get_access_token()
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return 1

    print(f"✅ Login saved to {cfg.SPOTIFY_CACHE_PATH}")
    print("   Keep that file private.")

    # A quick check so you know straight away what to expect.
    try:
        sp = spotipy.Spotify(auth_manager=auth, requests_timeout=8)
        me = sp.current_user()
        plan = me.get("product", "unknown")
        print(f"Account: {me.get('display_name') or me.get('id')} (plan: {plan})")
        if plan != "premium":
            print("ℹ️ Spotify only lets apps remote-control playback on Premium accounts. On a free account Remielle will "
                  "open the song in the Spotify app and press play for you instead.")
        devices = sp.devices().get("devices", [])
        if devices:
            print("Devices Spotify can see right now: " + ", ".join(f"{d['name']} ({d['type']})" for d in devices))
        else:
            print("No Spotify device is open right now. Remielle will start the desktop app when you ask for a song.")
    except Exception as e:
        print(f"(Could not run the account check: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
