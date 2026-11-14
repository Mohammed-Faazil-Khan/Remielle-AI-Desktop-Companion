@echo off
setlocal EnableExtensions
title Remielle - Full Stack Launcher

:: ============================================================
::  SETTINGS - edit these paths/switches if something moves
:: ============================================================
set "PROJECT_DIR=C:\Users\Faazil Khan\PycharmProjects\AI ANIME assistant"
set "PYEXE=C:\Users\Faazil Khan\PycharmProjects\.venv\Scripts\python.exe"
set "SOVITS_DIR=C:\GPT-SoVITS\GPT-SoVITS-v4-20250422fix"
set "SOVITS_PORT=9880"

:: Seconds to wait for GPT-SoVITS to start listening (loading its models can take a while)
set "SOVITS_TIMEOUT=150"

:: 1 = on, 0 = off
set "RUN_PREFLIGHT=1"
set "WARMUP_TTS=1"
set "CLOSE_SOVITS_ON_EXIT=1"
set "LOG_TO_FILE=0"
set "AVATAR_DEBUG=1"

:: Minecraft companion: 1 = also open her game every time she starts (Launch_Minecraft.bat does the work).
:: Leave it 0 and just say "Minecraft mode on" - that starts the server, her bot and her own goals together.
set "START_MINECRAFT=0"

:: Optional speed tweaks for a 6 GB GPU (remove the "rem " in front to use):
rem set "WHISPER_DEVICE=cpu"
rem set "SOVITS_SAMPLE_STEPS=8"
:: ============================================================

set "STARTED_SOVITS=0"
set "STARTED_MC=0"
set "APP_EXIT=0"

:: Make Python print emoji/UTF-8 correctly and tell voice_tts.py which port the voice server uses
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "SOVITS_URL=http://127.0.0.1:%SOVITS_PORT%/tts"

echo ========================================
echo   Starting Remielle (Cloud Brain + Local TTS)
echo ========================================
echo.

:: ------------------------------------------------------------
:: [1/6] Check that everything is where we expect it
:: ------------------------------------------------------------
echo [1/6] Checking folders and files...
if exist "%PROJECT_DIR%\app.py" goto chk_project_ok
echo [ERROR] app.py was not found in: %PROJECT_DIR%
echo         Fix PROJECT_DIR at the top of this file.
goto fail
:chk_project_ok

if exist "%SOVITS_DIR%\api_v2.py" goto chk_sovits_ok
echo [ERROR] api_v2.py was not found in: %SOVITS_DIR%
echo         Fix SOVITS_DIR at the top of this file.
goto fail
:chk_sovits_ok

if exist "%SOVITS_DIR%\runtime\python.exe" goto chk_sovits_py_ok
echo [ERROR] GPT-SoVITS runtime\python.exe is missing in: %SOVITS_DIR%
goto fail
:chk_sovits_py_ok

if exist "%PYEXE%" goto chk_pyexe_ok
echo [WARN] Project interpreter not found at: %PYEXE%
echo        Falling back to the "python" on your PATH.
set "PYEXE=python"
:chk_pyexe_ok
echo        Project : %PROJECT_DIR%
echo        Python  : %PYEXE%
echo        Voice   : %SOVITS_DIR%

cd /d "%PROJECT_DIR%"

:: ------------------------------------------------------------
:: [2/6] Python packages, API key, Spotify login
:: ------------------------------------------------------------
echo.
echo [2/6] Checking packages and settings...
"%PYEXE%" -c "import importlib.util as u,sys;m=[n for n in ('fastapi','uvicorn','webview','faster_whisper','pyaudio','openai','requests','numpy') if u.find_spec(n) is None];print('[ERROR] Missing required packages:',' '.join(m)) if m else None;sys.exit(1 if m else 0)"
if errorlevel 1 goto missing_pkgs

"%PYEXE%" -c "import importlib.util as u;m=[n for n in ('pyautogui','cv2','wikipedia','spotipy','pynput') if u.find_spec(n) is None];print('[WARN] Optional packages not installed (those features are disabled):',' '.join(m)) if m else None"

if defined NVIDIA_API_KEY goto key_ok
if not exist ".env" goto key_missing
findstr /R /C:"^NVIDIA_API_KEY=[^ ]" ".env" >nul 2>&1
if not errorlevel 1 goto key_ok
:key_missing
echo [WARN] No NVIDIA_API_KEY found (not in the environment and not in .env).
echo        Remielle will start, but her brain will say the key is missing.
:key_ok

if exist ".spotify_cache" goto spotify_ok
echo [INFO] Spotify is not linked yet. Run spotify_login.py once to let her play songs.
echo        Until then she opens Spotify's search page instead.
:spotify_ok

if exist "certs\remielle.pem" goto cert_ok
echo [INFO] No local certificate yet - your phone can see her on the Wi-Fi, but its microphone
echo        stays off. Run Setup_App.bat once (it also puts her in the Start Menu), then restart her.
:cert_ok

:: ------------------------------------------------------------
:: [3/6] GPT-SoVITS (the voice)
:: ------------------------------------------------------------
echo.
echo [3/6] Voice server (GPT-SoVITS) on port %SOVITS_PORT%...
netstat -ano | findstr /R /C:":%SOVITS_PORT% .*LISTENING" >nul 2>&1
if errorlevel 1 goto sovits_start
echo        Already running - not starting a second copy.
goto sovits_up

:sovits_start
echo        Starting it in its own window (watch that window if anything goes wrong)...
start "GPT-SoVITS Voice Server" /d "%SOVITS_DIR%" cmd /k "runtime\python.exe api_v2.py -a 127.0.0.1 -p %SOVITS_PORT%"
set "STARTED_SOVITS=1"

set /a WAITED=0
echo        Waiting for it to be ready (up to %SOVITS_TIMEOUT% seconds)...
:sovits_wait
netstat -ano | findstr /R /C:":%SOVITS_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto sovits_up
if %WAITED% GEQ %SOVITS_TIMEOUT% goto sovits_timeout
timeout /t 2 /nobreak >nul
set /a WAITED+=2
goto sovits_wait

:sovits_timeout
echo [ERROR] GPT-SoVITS did not start listening on port %SOVITS_PORT% within %SOVITS_TIMEOUT% seconds.
echo         Look at the "GPT-SoVITS Voice Server" window for the real error.
echo         Without it Remielle can think and show text, but she cannot speak.
choice /c YN /t 20 /d Y /m "Start Remielle anyway"
if errorlevel 2 goto stop_sovits_and_end
goto sovits_done

:sovits_up
echo        Voice server is up.
:sovits_done

where nvidia-smi >nul 2>&1
if errorlevel 1 goto gpu_done
echo        GPU memory now (used, total):
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
:gpu_done


:: ------------------------------------------------------------
:: [Extra] Her game: Launch_Minecraft.bat owns the server and the bot.
:: ------------------------------------------------------------
if not "%START_MINECRAFT%"=="1" goto mc_done
echo.
echo [Extra] Minecraft companion...
if exist "Launch_Minecraft.bat" goto mc_launch
echo [WARN]  Launch_Minecraft.bat is missing - continuing without the game.
goto mc_done
:mc_launch
start "Remielle Minecraft Companion" /min cmd /c Launch_Minecraft.bat /auto
set "STARTED_MC=1"
echo        Her server and bot each get their own window.
echo        Join with Minecraft Java: Multiplayer ^> Direct Connection ^> localhost
echo        Hold F8 anywhere to talk to her while you play (REMIELLE_GAME_PTT_KEY in .env).
echo        To stop just her game later: Stop_Minecraft.bat
:mc_done

:: ------------------------------------------------------------
:: [4/6] Warm up the voice (the very first sentence is always the slowest)
:: ------------------------------------------------------------
echo.
if "%WARMUP_TTS%"=="1" goto warmup_run
echo [4/6] Voice warm-up skipped.
goto warmup_done
:warmup_run
echo [4/6] Warming up the voice and testing it (first run can take 10-30 seconds)...
"%PYEXE%" -c "import sys,voice_tts;sys.exit(0 if voice_tts.play_anime_voice('[happy] Systems online.') else 1)"
if errorlevel 1 goto warmup_failed
echo        Voice test OK.
goto warmup_done
:warmup_failed
echo [WARN] The voice test failed - read the error line above (usually the voice server or a reference .wav file).
:warmup_done

:: ------------------------------------------------------------
:: [5/6] Check the brain (one tiny real API call)
:: ------------------------------------------------------------
echo.
if "%RUN_PREFLIGHT%"=="1" goto brain_run
echo [5/6] Brain check skipped.
goto brain_done
:brain_run
echo [5/6] Checking the brain (NVIDIA API)...
"%PYEXE%" agent_brain.py --selftest
if not errorlevel 1 goto brain_done
echo.
echo [WARN] The brain check reported a problem (see the lines above).
choice /c YN /t 15 /d Y /m "Start Remielle anyway"
if errorlevel 2 goto stop_sovits_and_end
:brain_done

:: ------------------------------------------------------------
:: [6/6] Start the assistant itself (FastAPI + avatar window)
:: ------------------------------------------------------------
echo.
echo [6/6] Starting Remielle...
echo        (Hold SPACE in the window to talk - or F8 anywhere while playing Minecraft. Close the avatar window or press Ctrl+C here to stop.)
echo        (The box printed just below is the link for your phone - see PHONE.md. Say "Minecraft mode on" to bring her into the game.)
echo.
if "%LOG_TO_FILE%"=="1" goto run_logged

"%PYEXE%" app.py
set "APP_EXIT=%errorlevel%"
goto after_app

:run_logged
if not exist "logs" mkdir "logs"
echo === Run started %date% %time% === >> "logs\remielle.log"
echo        Also saving everything to: %PROJECT_DIR%\logs\remielle.log
powershell -NoProfile -Command "& '%PYEXE%' -u app.py 2>&1 | ForEach-Object { $s = [string]$_; $s; Add-Content -LiteralPath 'logs\remielle.log' -Value $s -Encoding UTF8 }; exit $LASTEXITCODE"
set "APP_EXIT=%errorlevel%"

:after_app
echo.
echo ========================================
if "%APP_EXIT%"=="0" goto exit_clean
echo   Remielle stopped with error code %APP_EXIT%.
echo   Scroll up for the error message (a Python traceback usually sits just above this line).
goto exit_report_done
:exit_clean
echo   Remielle closed normally.
:exit_report_done
echo ========================================

:stop_sovits_and_end
if not "%STARTED_MC%"=="1" goto close_sovits
echo Closing her game (the server saves first, this takes a few seconds)...
call Stop_Minecraft.bat /quiet
:close_sovits
if not "%STARTED_SOVITS%"=="1" goto end
if not "%CLOSE_SOVITS_ON_EXIT%"=="1" goto end
echo Closing the voice server window that this launcher started...
taskkill /FI "WINDOWTITLE eq GPT-SoVITS Voice Server*" /T /F >nul 2>&1
goto end

:missing_pkgs
echo         Install them into the project's interpreter, for example:
echo           "%PYEXE%" -m pip install fastapi uvicorn pywebview faster-whisper PyAudio openai requests numpy
goto fail

:fail
echo.
echo Launch aborted.

:end
echo.
pause
endlocal
