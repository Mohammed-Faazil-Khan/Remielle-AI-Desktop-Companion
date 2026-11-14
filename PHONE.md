# Remielle on your phone

She runs on the PC. Your phone gets the same page, her same voice and her same brain - it is not a second copy of her.

## Android - choose one of these two

### A. Nothing to install on the phone (works anywhere, but a new address each time)

1. Start her on the PC: **Launch_Remielle.bat**.
2. Open a second window and run **Launch_Tunnel.bat**. It prints a link like
   `https://brave-three-lions-a.trycloudflare.com/?token=xxxxxxxx`.
3. Type that link into **Chrome** on the phone. It is a long link, so it is easier to send it to
   yourself (WhatsApp/Email from the PC) and tap it.
4. Hold the round button; Chrome asks for the microphone - tap **Allow**. That is the whole setup.
5. Optional: Chrome **⋮ > Add to Home screen > Add** for a full-screen icon. Cloudflare gives a
   *different* address every time you run step 2, so the icon has to be re-added each session.

This works because Cloudflare's certificate is one every phone already trusts.

### B. One permanent home-screen icon (at home, on the same Wi-Fi - needs her certificate once)

The link is the `Your phone` line she prints, e.g. `https://192.168.29.65:8000/?token=...`.
A phone only hands the microphone to a page it trusts, and your PC's own certificate is trusted
only after you install its root on the phone. Skip it and she still answers on the phone screen -
you hear her, she cannot hear you.

1. **PC, once:** double-click **Setup_App.bat** and accept the admin prompt, then start her with
   **Launch_Remielle.bat**. The root certificate is the file `certs\rootCA.pem` in this folder.
2. **Move that one file to the phone** - any way you like: USB cable and copy it in the phone's
   Files app; send it to yourself in WhatsApp/Telegram and save it; upload it to Drive.
   It is a small text file; if an app renames it to `rootCA.txt`, rename it back to `rootCA.pem`.
3. **Set a screen lock** (PIN or pattern) if you have none - Android will not install a
   certificate without one.
4. **Install it:** open **Settings** and search for `CA certificate`, then tap
   **Install a certificate** > **CA certificate** > choose `rootCA.pem` > **Install**.
   Where that lives by hand: Pixel - Settings > Security & privacy > More security settings >
   Encryption and credentials; Samsung - Settings > Security and privacy > More security settings >
   Encryption and credentials. Android warns that a CA lets apps read your traffic - that is only
   true for whoever holds the matching private key, and that never leaves `certs\` on your PC.
   (On a school or work phone the option is greyed out - use A instead.)
5. **Open the link** from step 1 in Chrome. No "Not secure" badge and no warning page means the
   certificate took.
6. **Make it an app:** Chrome **⋮ > Add to Home screen > Add**. Open her from that icon from now
   on - it remembers the door key and loads full screen.
7. Hold the button to talk; she answers out loud from the phone's speaker.

Two things can break later, both easy:
- The icon stops loading → your PC's Wi-Fi address changed. Restart her, open the new `Your phone`
  link once, re-add it.
- The page complains about the certificate after an address change → run **Setup_App.bat** again
  (it issues the certificate for the current address), then re-open the link.

### iPhone
Run Setup_App.bat, then AirDrop or save `certs\rootCA.pem` and open it in **Files** >
Settings > **Downloaded Profile** > Install, then Settings > General > About >
**Certificate Trust Settings** > switch it on. Everything else is the same as Android B, except the
home-screen icon lives in Safari's Share sheet > **Add to Home Screen**.

## Every time
1. Double-click **Launch_Remielle.bat** (or the Remielle entry in the Start Menu).
2. Read the box it prints at the top of the window - the line that starts with `Your phone` is a complete link with her door key in it.
3. Open that link on a phone on the **same Wi-Fi**, then use the browser menu > **Add to Home screen**. From then on you tap the icon; it opens full-screen, cached, with the token already remembered.

Hold the round button to talk; she answers out loud on whichever screen you last spoke to. The PC keeps the SPACE key.

## Away from home
Run **Launch_Tunnel.bat** while she is up. It opens a free Cloudflare quick tunnel and prints an `https://...trycloudflare.com/?token=...` link that works on any network, and its microphone really is allowed because that certificate is a normal one. The address changes every time you run it, so re-open the link (or re-add the home-screen icon) each session. Close the "Remielle Tunnel" window to shut the door.

## Two screens, one brain
- Only one screen speaks at a time: the one you just talked to. The other still shows the words in silence.
- Hold-to-talk on one screen at a time; the second screen has to wait until she finishes.
- Anything that is not this computer needs the door key, so a stranger on your Wi-Fi cannot make her open programs, run a terminal command or speak through your speakers. If you ever think the link leaked, change `REMIELLE_ACCESS_TOKEN` in `.env` and restart her.

## Good to know
- She binds `0.0.0.0`, so the Windows firewall may ask once to allow Remielle on your network - answer "Private networks".
- The page needs `character.vrm` (about 17 MB) once; after that the phone loads it from its own cache.
- Tests for all of this: `python -m unittest discover -s tests`.
