@echo off
cd /d "%~dp0"
.venv\Scripts\activate.bat
python test_schedule_query.py
pause

