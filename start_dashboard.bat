@echo off
setlocal
cd /d "%~dp0"

echo === GTA CLIP COMMAND: local dashboard ===

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python 3.12 virtual environment...
    where py >nul 2>nul
    if errorlevel 1 (
        echo ERROR: Python Launcher was not found. Install Python 3.12 and try again.
        pause
        exit /b 1
    )
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo ERROR: Could not create the virtual environment.
        pause
        exit /b 1
    )
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env from .env.example.
    echo Edit .env and set a dashboard password of at least 16 characters
    echo and a session secret of at least 32 characters, then run this file again.
    pause
    exit /b 0
)

echo Installing or updating Python dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo ERROR: Could not update pip.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Dependency installation failed.
    pause
    exit /b 1
)

echo Starting dashboard. Press Ctrl+C in this window to stop it.
".venv\Scripts\python.exe" scripts\start_dashboard.py
if errorlevel 1 (
    echo Dashboard exited with an error. Check the message above and your .env settings.
    pause
    exit /b 1
)

endlocal
