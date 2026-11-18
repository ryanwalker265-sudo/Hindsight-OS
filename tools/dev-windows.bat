@echo off
rem Try Hindsight in your normal browser (no emulator needed).
rem Needs Python 3.9 or newer from https://www.python.org/downloads/
rem Data is kept in .hindsight-data; delete that folder to start fresh.
cd /d "%~dp0\.."
set PY=python
where py >nul 2>nul && set PY=py -3
echo Hindsight is starting. Your browser will open http://127.0.0.1:7070
echo Close this window to stop.
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:7070"
%PY% -m hindsightd --dev %*
pause
