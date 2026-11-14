@echo off
setlocal EnableExtensions
title Remielle - Minecraft setup (run once)
cd /d "%~dp0"

echo ========================================
echo   Minecraft companion setup (run once)
echo ========================================
echo.

where node >nul 2>&1
if errorlevel 1 goto no_node
node -e "process.exit(Number(process.versions.node.split('.')[0])>=22?0:1)"
if errorlevel 1 goto old_node

where java >nul 2>&1
if errorlevel 1 goto no_java

echo [1/3] Installing the bot's packages (needs internet, takes a minute)...
pushd "minecraft_bot"
call npm install --no-audit --no-fund
if errorlevel 1 goto npm_failed
popd

echo.
echo [2/3] Minecraft server licence
echo         The server needs you to accept Mojang's EULA: https://aka.ms/MinecraftEULA
choice /c YN /m "Have you read it and do you accept"
if errorlevel 2 goto eula_no
set "EULA_FLAG=--accept-eula"
goto run_setup
:eula_no
set "EULA_FLAG="
echo         Not accepted - the server will not start until you run this again and say Y.

:run_setup
echo.
echo [3/3] Downloading and preparing the server...
node "minecraft_bot\setup_server.mjs" %EULA_FLAG%
if errorlevel 1 goto setup_failed

echo.
echo ========================================
echo   Done! Next:
echo     1. Open .env and add:  MC_OWNER=YourMinecraftName   (the name you will play as)
echo     2. Start Launch_Remielle.bat  (set START_MINECRAFT=1 inside it)
echo     3. In Minecraft Java: Multiplayer ^> Direct Connection ^> localhost
echo ========================================
goto end

:no_node
echo [ERROR] Node.js is not installed. Install the LTS version from https://nodejs.org (version 22 or newer), then run this again.
goto end
:old_node
echo [ERROR] Your Node.js is older than version 22. Install the current LTS from https://nodejs.org, then run this again.
goto end
:no_java
echo [ERROR] Java is not installed. Install "Temurin 21" from https://adoptium.net, then run this again.
goto end
:npm_failed
popd
echo [ERROR] npm install failed. Check your internet connection and read the message above.
goto end
:setup_failed
echo [ERROR] Server setup failed. Read the message above.

:end
echo.
pause
endlocal
