@echo off
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%.venv\Scripts\python.exe" (
    "%ROOT%.venv\Scripts\python.exe" -m docconvert %*
) else (
    python -m docconvert %*
)
endlocal & exit /b %errorlevel%
