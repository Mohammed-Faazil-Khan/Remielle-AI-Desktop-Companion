@echo off
setlocal EnableExtensions
title Remielle Minecraft Companion

:: ============================================================
::  Her game: the Minecraft server + Remielle's own player.
::  Run this on its own, or let Launch_Remielle.bat start it for you.
::  Run Setup_Minecraft.bat once before the first time you use this.
:: ============================================================

set "PROJECT_DIR=C:\Users\Faazil Khan\PycharmProjects\AI ANIME assistant"
set "MC_PORT=25565"
set "MC_SERVER_RAM=3G"
set "BOT_API_PORT=3100"

:: /auto  = start everything and leave (used by Launch_Remielle.bat and by her "minecraft mode" switch)
set "AUTO=0"
if /i "%~1"=="/auto" set "AUTO=1"

chcp 65001 >nul
cd /d "%PROJECT_DIR%" >nul 2>&1
if exist "app.py" goto preflight
echo [ERROR] This folder is not the Remielle project: %PROJECT_DIR%
echo         Fix PROJECT_DIR at the top of this file.
goto fail

:: ------------------------------------------------------------
:: [1/4] Is everything where it should be?
:: ------------------------------------------------------------
:preflight
echo ========================================
echo   Remielle - Minecraft companion
echo ========================================
echo.
echo [1/4] Checking the game files...

if exist "minecraft_bot\node_modules" goto node_deps_ok
echo [ERROR] The bot's packages are not installed. Run Setup_Minecraft.bat once.
goto fail
:node_deps_ok

if exist "minecraft_server\paper.jar" goto paper_ok
echo [ERROR] minecraft_server\paper.jar is missing. Run Setup_Minecraft.bat once.
goto fail
:paper_ok

findstr /R /C:"^eula=true" "minecraft_server\eula.txt" >nul 2>&1
if not errorlevel 1 goto eula_ok
echo [ERROR] The Minecraft EULA is not accepted. Run Setup_Minecraft.bat and answer Y.
goto fail
:eula_ok

findstr /R /C:"^MC_OWNER=[^ ]" ".env" >nul 2>&1
if not errorlevel 1 goto owner_ok
if defined MC_OWNER goto owner_ok
echo [WARN]  MC_OWNER is not set in .env - she will not know which player is you.
echo         Add a line like  MC_OWNER=YourMinecraftName  to .env.
:owner_ok

where node >nul 2>&1
if not errorlevel 1 goto node_ok
echo [ERROR] "node" was not found on your PATH, so her game bot cannot run.
goto fail
:node_ok

where java >nul 2>&1
if not errorlevel 1 goto java_ok
echo [ERROR] "java" was not found on your PATH, so the Minecraft server cannot run.
goto fail
:java_ok

:: ------------------------------------------------------------
:: [2/4] The server
:: ------------------------------------------------------------
echo.
echo [2/4] Minecraft server on port %MC_PORT%...
netstat -ano | findstr /R /C:":%MC_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto server_already
echo        Starting it in its own window...
start "Minecraft Server (Remielle)" /d "minecraft_server" cmd /k "java -Xms1G -Xmx%MC_SERVER_RAM% -jar paper.jar --nogui"
set "STARTED_SERVER=1"
set /a WAITED=0
:server_wait
netstat -ano | findstr /R /C:":%MC_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto server_ready
if %WAITED% GEQ 120 goto server_slow
timeout /t 2 /nobreak >nul
set /a WAITED+=2
goto server_wait
:server_slow
echo [ERROR] The server was not ready within 120 seconds - look at its window for the reason.
goto fail_close
:server_already
echo        A server is already listening on %MC_PORT% - using that one.
:server_ready
echo        Server is up.

:: ------------------------------------------------------------
:: [3/4] Her bot
:: ------------------------------------------------------------
echo.
echo [3/4] Her game bot (control API on port %BOT_API_PORT%)...
netstat -ano | findstr /R /C:":%BOT_API_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto bot_already
echo        Starting her player...
start "Remielle Minecraft Bot" /d "minecraft_bot" cmd /k "node bot.js"
set "STARTED_BOT=1"
set /a BWAIT=0
:bot_wait
if exist "%TEMP%\remielle_bot_health.txt" del "%TEMP%\remielle_bot_health.txt" >nul 2>&1
curl -s -m 2 "http://127.0.0.1:%BOT_API_PORT%/health" > "%TEMP%\remielle_bot_health.txt" 2>nul
findstr /C:"ok" "%TEMP%\remielle_bot_health.txt" >nul 2>&1
if not errorlevel 1 goto bot_ready
if %BWAIT% GEQ 60 goto bot_slow
timeout /t 2 /nobreak >nul
set /a BWAIT+=2
goto bot_wait
:bot_slow
echo [WARN]  Her control API did not answer within 60 seconds. Read the bot window.
goto bot_ready
:bot_already
echo        Her bot is already running - not starting a second copy.
:bot_ready

:: ------------------------------------------------------------
:: [4/4] Ready
:: ------------------------------------------------------------
echo.
echo [4/4] She is in the game.
echo        Join with Minecraft Java: Multiplayer ^> Direct Connection ^> localhost
echo        Hold F8 anywhere to talk to her while you play (REMIELLE_GAME_PTT_KEY in .env).
echo        Tip: type  op YourName  in the server window once so you can use commands.
echo        Ask her to do something she has to work out ("sit on the boat", "build me a shelter")
echo        and say "explore on your own" if you want her to keep herself busy.
if "%AUTO%"=="1" goto end_auto
echo.
echo ========================================
echo   Leave this window open while you play.
echo   Press any key in this window to stop her game.
echo ========================================
pause >nul
call "%~dp0Stop_Minecraft.bat" /quiet
echo This window can close now.
pause >nul
endlocal
exit /b 0

:end_auto
echo.
echo (Started in background mode - close her game with Stop_Minecraft.bat.)
endlocal
exit /b 0

:fail_close
call "%~dp0Stop_Minecraft.bat" /quiet
:fail
echo.
if "%AUTO%"=="1" exit /b 1
pause
endlocal
exit /b 1
