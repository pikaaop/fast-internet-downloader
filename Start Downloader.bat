@echo off
cd /d "%~dp0"
python downloader.py
if errorlevel 1 pause
