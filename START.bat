@echo off
echo ============================================
echo   Pickleball Sniper - Setup and Launch
echo ============================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed.
    echo.
    echo Please install Python first:
    echo   1. Go to https://www.python.org/downloads/
    echo   2. Click "Download Python 3.x.x"
    echo   3. Run the installer
    echo   4. IMPORTANT: Check "Add Python to PATH" at the bottom
    echo   5. Click "Install Now"
    echo   6. After install, close this window and double-click START.bat again
    echo.
    pause
    exit /b 1
)

echo Python found!
python --version
echo.

:: Pull latest code from GitHub
echo Pulling latest updates from GitHub...
git pull
echo.

:: Install dependencies
echo Installing dependencies...
pip install streamlit requests cryptography
echo.

:: Launch the app
echo ============================================
echo   Launching Pickleball Sniper...
echo   Your browser will open automatically.
echo   Keep this window open while using the app.
echo ============================================
echo.
streamlit run main.py
pause
