@echo off
setlocal enabledelayedexpansion

echo ================================================================
echo   AirPods Battery for Windows ^| Build Script
echo ================================================================
echo.

:: ── Working directory: must be the project root ───────────────────
if not exist "airpods_battery.py" (
    echo ERROR: Run build.bat from the airpods-battery-win project root.
    echo        Current directory: %CD%
    pause & exit /b 1
)

:: ── Prerequisites ─────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found in PATH
    pause & exit /b 1
)

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: PyInstaller not found.  Run:  python -m pip install pyinstaller
    pause & exit /b 1
)

:: ── Step 1: Generate the .ico file ───────────────────────────────
echo [1/3] Generating icon...
python assets\generate_ico.py
if errorlevel 1 (
    echo ERROR: Icon generation failed.
    pause & exit /b 1
)
echo       OK — assets\airpods_icon.ico
echo.

:: ── Step 2: Clean previous artefacts ────────────────────────────
echo [2/3] Cleaning previous build...
if exist "dist\AirPodsBattery.exe" (
    del /f /q "dist\AirPodsBattery.exe"
    echo       Deleted dist\AirPodsBattery.exe
)
if exist "build" (
    rmdir /s /q "build"
    echo       Deleted build\
)
if exist "AirPodsBattery.spec" (
    del /f /q "AirPodsBattery.spec"
    echo       Deleted AirPodsBattery.spec
)
echo.

:: ── Step 3: PyInstaller ───────────────────────────────────────────
echo [3/3] Running PyInstaller...
echo.

python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --noconfirm ^
    --name "AirPodsBattery" ^
    --icon "assets\airpods_icon.ico" ^
    --add-data "assets;assets" ^
    --collect-all bleak ^
    --collect-all pystray ^
    --hidden-import "winreg" ^
    --hidden-import "bleak.backends.winrt" ^
    --hidden-import "bleak.backends.winrt.scanner" ^
    --hidden-import "bleak.backends.winrt.client" ^
    --hidden-import "PIL._tkinter_finder" ^
    --exclude-module "matplotlib" ^
    --exclude-module "numpy" ^
    --exclude-module "scipy" ^
    --exclude-module "pandas" ^
    --exclude-module "IPython" ^
    --exclude-module "tkinter.test" ^
    --log-level "WARN" ^
    "airpods_battery.py"

if errorlevel 1 (
    echo.
    echo ================================================================
    echo   ERROR: PyInstaller build failed.
    echo   Check the output above for details.
    echo ================================================================
    pause & exit /b 1
)

:: ── Report size ───────────────────────────────────────────────────
echo.
echo ================================================================
for %%F in ("dist\AirPodsBattery.exe") do (
    set /a SIZE_KB=%%~zF / 1024
    echo   Build complete!
    echo.
    echo   Output : dist\AirPodsBattery.exe
    echo   Size   : !SIZE_KB! KB
)
echo.
echo   To install autostart, run the EXE and enable
echo   "Start with Windows" from the tray menu.
echo ================================================================
echo.
pause
