@echo off
setlocal EnableExtensions
title Stop Remielle Minecraft Companion

:: ============================================================
::  Stops her game bot and the Minecraft server started by
::  Launch_Minecraft.bat. The server gets time to save the world.
::  Pass /quiet to use it from another script (no pause).
:: ============================================================

chcp 65001 >nul
set "QUIET=0"
if /i "%~1"=="/quiet" set "QUIET=1"

echo Stopping Remielle's game...

tasklist /FI "WINDOWTITLE eq Remielle Minecraft Bot*" 2>nul | findstr /I "cmd.exe node.exe" >nul 2>&1
if not errorlevel 1 goto kill_bot
echo   (she is not running)
goto server_check
:kill_bot
echo   closing her bot window...
taskkill /FI "WINDOWTITLE eq Remielle Minecraft Bot*" /T /F >nul 2>&1

:server_check
tasklist /FI "WINDOWTITLE eq Minecraft Server (Remielle)*" 2>nul | findstr /I "cmd.exe java.exe" >nul 2>&1
if not errorlevel 1 goto stop_server
echo   (no server window from this launcher)
goto done
:stop_server
echo   asking the server to save and stop (up to 20 seconds)...
taskkill /FI "WINDOWTITLE eq Minecraft Server (Remielle)*" /T >nul 2>&1
set /a N=0
:wait_stop
tasklist /FI "WINDOWTITLE eq Minecraft Server (Remielle)*" 2>nul | findstr /I "java.exe" >nul 2>&1
if errorlevel 1 goto done
if %N% GEQ 20 goto force_stop
timeout /t 2 /nobreak >nul
set /a N+=2
goto wait_stop
:force_stop
echo   the server did not close itself - ending it now.
taskkill /FI "WINDOWTITLE eq Minecraft Server (Remielle)*" /T /F >nul 2>&1

:done
echo Done.
if "%QUIET%"=="1" endlocal & exit /b 0
pause
endlocal
