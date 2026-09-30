@echo off
REM ============================================================
REM Douyin Spark Assistant - Windows backend launcher
REM
REM All real logic lives in start-backend.ps1: it finds a usable
REM Python, prepares .venv, installs requirements, starts backend.py.
REM This file only forwards, so the two entry points cannot drift.
REM
REM NOTE: keep this file ASCII-only. cmd.exe reads .bat in the OEM
REM code page (GBK on zh-CN Windows), so UTF-8 Chinese here would
REM show up as mojibake, and a UTF-8 BOM would corrupt the first line.
REM ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-backend.ps1" %*
exit /b %errorlevel%
