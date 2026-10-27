@echo off
title Versa SD-WAN Router Health and Ping Auditor - Tata Communications
cd /d "%~dp0"

echo ======================================================================
echo    Tata Communications - Versa SD-WAN Router Health Auditor
echo ======================================================================
echo.

:: 1. Check if python is in PATH
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [+] Starting with system Python...
    python versa_gui.py
    if %ERRORLEVEL% equ 0 goto :eof
)

:: 2. Check py launcher
where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [+] Starting with 'py' launcher...
    py versa_gui.py
    if %ERRORLEVEL% equ 0 goto :eof
)

:: 3. Check current user's uv python
if exist "%APPDATA%\uv\python\cpython-3.12-windows-x86_64-none\python.exe" (
    echo [+] Starting with uv Python...
    "%APPDATA%\uv\python\cpython-3.12-windows-x86_64-none\python.exe" versa_gui.py
    if %ERRORLEVEL% equ 0 goto :eof
)

:: 4. Check common Python install locations
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
) do (
    if exist "%%~P" (
        echo [+] Found Python at %%~P
        "%%~P" versa_gui.py
        if %ERRORLEVEL% equ 0 goto :eof
    )
)

echo.
echo [!] ERROR: Python was not found on this computer.
echo [*] Please ensure Python 3.10+ is installed and added to PATH,
echo     or install Python from company Software Center.
echo.
pause
