# Playing Minecraft with Remielle

She joins your own private Minecraft world as a second player, follows you, fights, collects things and chats, and you control her by voice.

## One-time setup
1. Install **Node.js 22+** (nodejs.org, LTS) and **Java 21** (adoptium.net, "Temurin 21"). Java is only for the server.
2. `pip install pynput` in your project interpreter (gives you the hold-to-talk key that works while Minecraft is in front).
3. Put `MC_OWNER=YourMinecraftName` in `.env` (the name you play as). She obeys only this player.
4. Double-click **Setup_Minecraft.bat**. It installs the bot, downloads the Paper server and asks you to accept Mojang's EULA (https://aka.ms/MinecraftEULA).
5. Nothing to switch on. `Launch_Remielle.bat` no longer starts Minecraft by itself - you bring her into the game when you want her there (see below).

## Every time
1. Start **Launch_Remielle.bat**. It starts the voice, the avatar and her phone page.
2. Bring her into the game, one of three ways:
   - say **"Minecraft mode on"** (or "join the game", "play minecraft by yourself"),
   - click the **MINECRAFT: OFF** chip in the top-right of her window (it works from your phone too),
   - or double-click **Launch_Minecraft.bat** if you only want the server and the bot.
3. In Minecraft **Java Edition 1.21.11** (create a 1.21.11 profile in the launcher): Multiplayer > Direct Connection > `localhost`.
4. Hold **F8** anywhere (or SPACE in her window, or the button on your phone) and talk. In the server window, type `op YourName` once if you want commands.

## Minecraft mode
The one switch does all three things at once: it starts the server and her bot if they are not running, waits until she is standing in the world, tells you where she landed, and turns her own goals on so she gathers, crafts, tidies and explores while you are quiet.

**Minecraft mode off** stops her picking goals and leaves the running world alone - closing the server is your decision. Use **Stop_Minecraft.bat** for that. Say **"minecraft mode status"** (or "are you in the game") to ask where she is.

## What you can say
follow me, come here, stay, stop, guard me, attack the zombie, hunt cows, mine iron, get me 10 oak logs, craft a pickaxe, go to 10 64 -20, go home, set home here, give me 5 cobblestone, eat, sleep, what's in your inventory, how are you doing, where are you, tell everyone hello.

You can also type in the game chat: `!follow`, `!collect iron 5`, or just say "Remielle ..." and she answers in character. She speaks up on her own when she sees a monster, gets hurt, runs low on health, dies, or finishes a job. A creeper makes her run away.

## Good to know
- The server and bot only listen on this PC (127.0.0.1). Nobody else can join or control her.
- Mining ore needs the right pickaxe in her bag (ask her to craft one first); she will tell you if she can't break something.
- If something breaks, look at the "Minecraft Server (Remielle)" and "Remielle Minecraft Bot" windows. The bot reconnects by itself if the server restarts.
- Tests: `cd minecraft_bot && node --test test/*.test.js` and `python -m unittest discover -s tests`.

## Honest status
Tested against a simulated 1.21.4 server: joining, commands, mining, giving, chat, security, reconnecting, and all game logic with a simulated world. Not testable here (no internet): downloading the real Paper server, real monsters, crafting and sleeping. If one of those misbehaves, tell me what the bot window says.
