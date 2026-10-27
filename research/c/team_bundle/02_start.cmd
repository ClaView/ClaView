@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Run 01_install.cmd first.
 pause
 exit /b 1
)
cd /d "%~dp0c_practice\abc_integration"
"%~dp0.venv\Scripts\python.exe" -X utf8 launcher.py
pause
