@echo off
cd /d "%~dp0"
.venv\Scripts\activate.bat
python check_schedule_types.py
pause

