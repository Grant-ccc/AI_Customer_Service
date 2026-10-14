@echo off
chcp 65001 >nul
pushd "%~dp0.."
".venv\Scripts\python.exe" -X utf8 scripts\qq_service.py start
set "QQ_RESULT=%ERRORLEVEL%"
if not "%QQ_RESULT%"=="0" pause
popd
exit /b %QQ_RESULT%
