@echo off
cd /d "%~dp0"
python manage.py backup_ged
if errorlevel 1 exit /b 1
