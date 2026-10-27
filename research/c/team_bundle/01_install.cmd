@echo off
cd /d "%~dp0"
py -3.12 -X utf8 setup_team.py
if errorlevel 1 (
 echo Installation failed. Install Python 3.12 and check internet access.
)
pause
