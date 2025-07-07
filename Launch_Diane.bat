@echo off
Title Diane AI (GUI Launcher)

:: ------------------------------------------------------------------
::  Launcher for Diane AI (GUI Version)
::  - This script navigates to the correct folder and runs the main
::    diane_script.py, which will now launch the graphical window.
:: ------------------------------------------------------------------

:: 1. Navigate to this script's own directory.
cd /d "%~dp0"

:: 2. Check if the virtual environment has been created.
if not exist "diane_env\Scripts\python.exe" (
    echo [ERROR] Virtual environment 'diane_env' not found or incomplete.
    echo Please run the 'diane_installers.bat' setup script first to install everything.
    pause
    exit /b
)

:: 3. Run the Python script using the venv's python.exe
::    diane_script.py will handle creating and showing the GUI window.
echo [INFO] Starting Diane AI... The GUI window will appear shortly.
"diane_env\Scripts\python.exe" diane_script.py

:: 4. This part of the script will only run after the GUI window is closed.
echo.
echo --- Diane AI has been closed. Press any key to exit the console. ---
pause