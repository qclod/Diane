@echo off
Title Diane AI - Full Launcher (Revised)

:: ------------------------------------------------------------------
:: 1. Check for Administrator privileges.
:: ------------------------------------------------------------------
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if '%errorlevel%' NEQ '0' (
    echo Requesting administrative privileges to run Diane...
    powershell.exe -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: ------------------------------------------------------------------
:: 2. Set the working directory to the script's location.
:: ------------------------------------------------------------------
cd /d "%~dp0"
cls
echo [SUCCESS] Running as Administrator in: %cd%
echo.

:: ------------------------------------------------------------------
:: 3. Create the virtual environment if it doesn't exist.
:: ------------------------------------------------------------------
echo [INFO] Checking for virtual environment 'diane_env'...
if not exist "diane_env" (
    echo [SETUP] Creating virtual environment 'diane_env'...
    python -m venv diane_env
)

:: ------------------------------------------------------------------
:: 4. Install libraries using requirements.txt
:: ------------------------------------------------------------------
echo.
echo [ACTION] Installing/Verifying libraries from requirements.txt...
"diane_env\Scripts\pip.exe" install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to install Python libraries. Please check your internet connection and run the script again.
    pause
    exit /b %errorlevel%
)

echo.
echo [INFO] Pip install command finished.

:: ------------------------------------------------------------------
:: 5. Run the Python script using the EXPLICIT path to python.exe
:: ------------------------------------------------------------------
echo.
echo ===================================
echo  Environment ready. Starting Diane...
echo ===================================
echo.

"diane_env\Scripts\python.exe" diane_script.py

:: ------------------------------------------------------------------
:: 6. Keep the window open after the script is done.
:: ------------------------------------------------------------------
echo.
echo =====================================
echo  Diane script has finished.
echo =====================================
pause
