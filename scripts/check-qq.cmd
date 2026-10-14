@echo off
chcp 65001 >nul
pushd "%~dp0.."
".venv\Scripts\python.exe" -X utf8 scripts\qq_service.py status
pause
popd
