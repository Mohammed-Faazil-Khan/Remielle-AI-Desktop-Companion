@echo off
setlocal EnableExtensions
title Remielle - One-time App Setup

:: ============================================================
::  Run this ONCE. It gets the two small tools she needs, makes the local
::  https certificate your phone's microphone depends on, and puts Remielle
::  in the Start Menu. After this, Launch_Remielle.bat is all you ever run.
::  Nothing here touches your files or your other programs.
:: ============================================================

set "PROJECT_DIR=%~dp0"
set "MKCERT_VERSION=v1.4.4"
set "TOOLS_DIR=%PROJECT_DIR%tools"
set "CERT_DIR=%PROJECT_DIR%certs"
set "MKCERT=%TOOLS_DIR%\mkcert.exe"
set "CLOUDFLARED=%TOOLS_DIR%\cloudflared.exe"
set "PYEXE=C:\Users\Faazil Khan\PycharmProjects\.venv\Scripts\python.exe"

echo ========================================
echo   Remielle - one-time app setup
echo ========================================
echo.

cd /d "%PROJECT_DIR%"

:: ------------------------------------------------------------
:: [1/5] Python, so she can tell us this computer's own address
:: ------------------------------------------------------------
echo [1/5] Checking the project's Python...
if exist "%PYEXE%" goto py_ok
echo [WARN] Project interpreter not found at:
echo        %PYEXE%
echo        Fix PYEXE at the top of this file, or she will not know this PC's LAN address.
set "PYEXE=python"
:py_ok
"%PYEXE%" -c "import sys" >nul 2>&1
if errorlevel 1 goto fail
for /f "usebackq delims=" %%I in (`"%PYEXE%" -c "import device_access; print(device_access.local_ip())"`) do set "LAN_IP=%%I"
echo        This computer's address on your Wi-Fi: %LAN_IP%

:: ------------------------------------------------------------
:: [2/5] mkcert - the tool that makes a certificate a phone will trust
:: ------------------------------------------------------------
echo.
echo [2/5] Local https certificate...
if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"
if exist "%MKCERT%" goto mkcert_have
echo        Downloading mkcert %MKCERT_VERSION% into tools\ ...
curl -L --fail -o "%MKCERT%" "https://github.com/FiloSottile/mkcert/releases/download/%MKCERT_VERSION%/mkcert-%MKCERT_VERSION%-windows-amd64.exe"
if errorlevel 1 goto mkcert_download_failed
:mkcert_have

if not exist "%CERT_DIR%" mkdir "%CERT_DIR%"
echo        Making the certificate for localhost and %LAN_IP% (Windows may ask you once to allow the root)...
"%MKCERT%" -install
if errorlevel 1 goto mkcert_install_failed
"%MKCERT%" -cert-file "%CERT_DIR%\remielle.pem" -key-file "%CERT_DIR%\remielle-key.pem" localhost 127.0.0.1 ::1 %LAN_IP% >nul
if errorlevel 1 goto mkcert_sign_failed

:: The phone has to be told to trust the little authority that signed that certificate.
for /f "usebackq delims=" %%I in (`"%MKCERT%" -CAROOT`) do set "CAROOT=%%I"
if exist "%CAROOT%\rootCA.pem" copy /y "%CAROOT%\rootCA.pem" "%CERT_DIR%\rootCA.pem" >nul
echo        Certificate: %CERT_DIR%\remielle.pem
echo        Root to copy to the phone: %CERT_DIR%\rootCA.pem
goto step3

:mkcert_download_failed
echo [WARN] Could not download mkcert (no internet, or the link moved).
echo        She still works on this PC over plain http - only the phone microphone needs https.
goto step3

:mkcert_install_failed
echo [WARN] mkcert -install did not run. Close any security prompt and try this file again.
goto step3

:mkcert_sign_failed
echo [WARN] The certificate was not made. She stays on plain http; the phone microphone will not work.

:: ------------------------------------------------------------
:: [3/5] cloudflared - the door she uses when you are out
:: ------------------------------------------------------------
:step3
echo.
echo [3/5] Away-from-home tunnel tool...
if exist "%CLOUDFLARED%" goto tunnel_have
echo        Downloading cloudflared into tools\ ...
curl -L --fail -o "%CLOUDFLARED%" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
if errorlevel 1 goto tunnel_failed
:tunnel_have
echo        Launch_Tunnel.bat can now print a link that works anywhere.
goto tools

:tunnel_failed
echo [WARN] Could not download cloudflared. Launch_Tunnel.bat will try again when you run it.

:: ------------------------------------------------------------
:: [4/5] An app in the Start Menu, with her own icon
:: ------------------------------------------------------------
:tools
echo.
echo [4/5] Start Menu shortcut...
if not exist "%PROJECT_DIR%static\icons\remielle.ico" echo [WARN] static\icons\remielle.ico is missing - the shortcut will use a plain icon.
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $p = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Remielle.lnk'; $s = $ws.CreateShortcut($p); $s.TargetPath = '%PROJECT_DIR%Launch_Remielle.bat'; $s.WorkingDirectory = '%PROJECT_DIR%'; $s.IconLocation = '%PROJECT_DIR%static\icons\remielle.ico,0'; $s.Description = 'Remielle - anime assistant and Minecraft companion'; $s.Save(); Write-Host ('        ' + $p)"
if errorlevel 1 echo [WARN] The shortcut could not be made. Launch_Remielle.bat still works by double-click.

:: ------------------------------------------------------------
:: [5/5] What to do on the phone
:: ------------------------------------------------------------
echo.
echo [5/5] Once on your phone (only needed for the phone microphone):
echo        1. Copy certs\rootCA.pem to the phone (USB, WhatsApp it to yourself, or Drive).
echo           It is a text file - if the app renames it to rootCA.txt, name it back to .pem.
echo        2. Android: Settings, search for "CA certificate" ^> Install a certificate ^>
echo           CA certificate ^> pick rootCA.pem ^> Install. Needs a screen lock (PIN) first.
echo           iPhone:  open the file in Files, then Settings ^> Downloaded Profile ^> Install,
echo                    then Settings ^> General ^> About ^> Certificate Trust Settings ^> turn it on.
echo        3. Run Launch_Remielle.bat, open the link it prints, menu ^> Add to Home screen.
echo.
echo        Prefer to skip all of that? Run Launch_Tunnel.bat while she is up and use the
echo        https link it prints - it is trusted already, so the microphone works at once.
echo        Step by step for both ways: PHONE.md
echo.
echo ========================================
echo   Setup finished. Start her with Launch_Remielle.bat.
echo   (She speaks https as soon as certs\remielle.pem exists.)
echo ========================================
goto end

:fail
echo.
echo [ERROR] "%PYEXE%" does not run. Fix PYEXE at the top of this file.

:end
echo.
pause
endlocal
