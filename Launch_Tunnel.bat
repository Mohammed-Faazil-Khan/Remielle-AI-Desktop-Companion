@echo off
setlocal EnableExtensions
title Remielle - Away From Home Tunnel

:: ============================================================
::  Run Launch_Remielle.bat first, then this.
::  It opens one free Cloudflare "quick tunnel" to her page and prints a
::  normal https link you can open on any phone, anywhere - no Wi-Fi match
::  needed, and the microphone works because the certificate is a real one.
::  The link changes every time you run this file.
:: ============================================================

set "PROJECT_DIR=%~dp0"
set "PYEXE=C:\Users\Faazil Khan\PycharmProjects\.venv\Scripts\python.exe"
set "CLOUDFLARED=%PROJECT_DIR%tools\cloudflared.exe"
set "LOG=%TEMP%\remielle_tunnel.log"
set "TUNNEL_TIMEOUT=90"

cd /d "%PROJECT_DIR%"

if exist "%PYEXE%" goto py_ok
set "PYEXE=python"
:py_ok

echo ========================================
echo   Remielle - tunnel for when you are out
echo ========================================
echo.

echo [1/4] Reading her address, port and door key...
for /f "usebackq delims=" %%I in (`"%PYEXE%" -c "import remielle_config as c; print(c.APP_PORT)"`) do set "TUNNEL_PORT=%%I"
for /f "usebackq delims=" %%I in (`"%PYEXE%" -c "import remielle_config as c; print('1' if c.cert_paths()[0] else '0')"`) do set "TUNNEL_HTTPS=%%I"
for /f "usebackq delims=" %%I in (`"%PYEXE%" -c "import device_access as d; print(d.ensure_token())"`) do set "TUNNEL_TOKEN=%%I"
if not defined TUNNEL_PORT set "TUNNEL_PORT=8000"
if not defined TUNNEL_HTTPS set "TUNNEL_HTTPS=0"
if not defined TUNNEL_TOKEN goto token_failed
echo        Port %TUNNEL_PORT%, https %TUNNEL_HTTPS%, door key found.

netstat -ano | findstr /R /C:":%TUNNEL_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto tunnel_tools
echo [WARN] Nothing is listening on port %TUNNEL_PORT% - start her with Launch_Remielle.bat.
echo        The link below still works as soon as she is up.

:: ------------------------------------------------------------
:: [2/4] The tunnel program itself
:: ------------------------------------------------------------
:tunnel_tools
echo.
echo [2/4] Tunnel program...
if exist "%CLOUDFLARED%" goto tunnel_have
if not exist "%PROJECT_DIR%tools" mkdir "%PROJECT_DIR%tools"
echo        Downloading cloudflared into tools\ (once)...
curl -L --fail -o "%CLOUDFLARED%" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
if errorlevel 1 goto tunnel_download_failed
:tunnel_have

:: ------------------------------------------------------------
:: [3/4] Open it
:: ------------------------------------------------------------
echo.
echo [3/4] Opening the tunnel (its own window - keep it open while you are out)...
del "%LOG%" >nul 2>&1
if not "%TUNNEL_HTTPS%"=="1" goto tunnel_plain_http
start "Remielle Tunnel" /min cmd /c ""%CLOUDFLARED%" tunnel --url https://127.0.0.1:%TUNNEL_PORT% --no-tls-verify 2>> "%LOG%""
goto tunnel_opened
:tunnel_plain_http
start "Remielle Tunnel" /min cmd /c ""%CLOUDFLARED%" tunnel --url http://127.0.0.1:%TUNNEL_PORT% 2>> "%LOG%""
:tunnel_opened

:: ------------------------------------------------------------
:: [4/4] Wait for the address it hands out
:: ------------------------------------------------------------
echo.
echo [4/4] Waiting for Cloudflare to answer (up to %TUNNEL_TIMEOUT% seconds)...
set /a WAITED=0
:tunnel_wait
set "TUNNEL_URL="
for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "if (Test-Path -LiteralPath '%LOG%') { $m = Select-String -Path '%LOG%' -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1; if ($m) { $m.Matches[0].Value } }"`) do set "TUNNEL_URL=%%U"
if defined TUNNEL_URL goto tunnel_ready
timeout /t 3 /nobreak >nul
set /a WAITED+=3
if %WAITED% GEQ %TUNNEL_TIMEOUT% goto tunnel_slow
goto tunnel_wait

:tunnel_ready
echo.
echo ========================================
echo   Your phone, anywhere:
echo     %TUNNEL_URL%/?token=%TUNNEL_TOKEN%
echo.
echo   Open that once, then menu ^> Add to Home screen.
echo   The home-screen app keeps working at home too - but it has to be
echo   re-added each time you run this file, because the address changes.
echo ========================================
echo.
echo Close the "Remielle Tunnel" window to shut the door again.
pause
goto end

:tunnel_slow
echo [ERROR] No address came back. Look in the "Remielle Tunnel" window -
echo         a company or hotel network usually blocks tunnels like this.
pause
goto end

:tunnel_download_failed
echo [ERROR] cloudflared could not be downloaded. Check your internet, then run this again.

:token_failed
echo [ERROR] Her door key could not be read or made. Run Launch_Remielle.bat once - it makes the key
echo         and saves it in .env - then try this file again.

:end
endlocal
