@echo off
title GPT-SoVITS Voice Server
cd /d C:\GPT-SoVITS\GPT-SoVITS-v4-20250422fix
echo Starting GPT-SoVITS on port 9880...
.\runtime\python api_v2.py -a 127.0.0.1 -p 9880
pause