@echo off
setlocal
cd /d "%~dp0"

set "TAILSCALE_EXE="
for /f "delims=" %%T in ('where tailscale.exe 2^>nul') do if not defined TAILSCALE_EXE set "TAILSCALE_EXE=%%T"
if not defined TAILSCALE_EXE if exist "%ProgramFiles%\Tailscale\tailscale.exe" set "TAILSCALE_EXE=%ProgramFiles%\Tailscale\tailscale.exe"

if not defined TAILSCALE_EXE (
    echo Tailscale was not found. Install and sign in to Tailscale, then run this file again.
    pause
    exit /b 1
)

echo Setting up a private HTTPS address for your local dashboard...
"%TAILSCALE_EXE%" serve --bg 8000
if errorlevel 1 (
    echo.
    echo Tailscale could not enable Serve. Open this file from an Administrator terminal and try again.
    pause
    exit /b 1
)

echo.
echo Your private backend address is shown above. Copy it into Settings on the Vercel website.
echo This Serve configuration persists across computer and Tailscale restarts.
echo It is available only to devices signed in to your Tailscale network.
pause
endlocal

