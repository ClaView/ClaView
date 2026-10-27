@echo off
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -X utf8 c_practice\abc_integration\launcher.py --stop
pause
