@echo off
REM ============================================================
REM ProteoSphere Model Studio -- one-time Windows Defender fix
REM ------------------------------------------------------------
REM Some Windows machines hit a state where Windows Defender's
REM real-time scan holds every python.exe spawn for 60+ seconds
REM while it inspects torch's hundreds of .pyd files. Symptom:
REM
REM   * `launch_model_studio.bat` hangs at "Checking Python
REM     dependencies..." with the cursor blinking
REM   * `python -c "import torch"` from a fresh shell takes
REM     minutes (or never returns)
REM   * Reinstalling, rebooting, virtualenvs don't help
REM
REM Fix: tell Defender to skip scanning python.exe + the torch
REM and nvidia site-packages directories. This script does both
REM in one shot. Run it ONCE. Subsequent launches of the
REM Model Studio will load torch in 1-2 seconds instead of
REM minutes.
REM
REM REQUIRES ADMINISTRATOR RIGHTS. If you double-click this and
REM see a UAC prompt, click Yes. If you don't see one, right-
REM click the .bat file and "Run as administrator".
REM ============================================================

setlocal EnableExtensions EnableDelayedExpansion

REM Self-elevate via UAC if not already running as admin.
net session >nul 2>&1
if errorlevel 1 (
    echo  This script needs administrator rights to update
    echo  Windows Defender exclusions. Re-launching with UAC prompt...
    echo.
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

echo.
echo  ============================================================
echo   ProteoSphere -- Windows Defender exclusion setup
echo  ============================================================
echo.
echo  Adding Defender exclusions:
echo    * python.exe   (every running python process)
echo    * pip.exe      (package installs)
echo.

REM Resolve python's site-packages so we can target torch + nvidia
REM directories specifically (more surgical than excluding all of
REM site-packages).
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

REM Try to derive site-packages from %PY%. Fall back to a guess.
for %%F in ("%PY%") do set "PY_DIR=%%~dpF"
if "%PY_DIR:~-1%"=="\" set "PY_DIR=%PY_DIR:~0,-1%"
set "SITE_PKG=%PY_DIR%\Lib\site-packages"

powershell -NoProfile -Command "Add-MpPreference -ExclusionProcess 'python.exe'"
powershell -NoProfile -Command "Add-MpPreference -ExclusionProcess 'pip.exe'"

if exist "%SITE_PKG%\torch" (
    echo    * %SITE_PKG%\torch
    powershell -NoProfile -Command "Add-MpPreference -ExclusionPath '%SITE_PKG%\torch'"
)
if exist "%SITE_PKG%\nvidia" (
    echo    * %SITE_PKG%\nvidia
    powershell -NoProfile -Command "Add-MpPreference -ExclusionPath '%SITE_PKG%\nvidia'"
)
if exist "%SITE_PKG%\torch_geometric" (
    echo    * %SITE_PKG%\torch_geometric
    powershell -NoProfile -Command "Add-MpPreference -ExclusionPath '%SITE_PKG%\torch_geometric'"
)

echo.
echo  Done. Verifying:
powershell -NoProfile -Command "(Get-MpPreference).ExclusionProcess | Where-Object { $_ -match 'python|pip' } | ForEach-Object { Write-Host '    process exclusion:' $_ }"
powershell -NoProfile -Command "(Get-MpPreference).ExclusionPath | Where-Object { $_ -match 'torch|nvidia' } | ForEach-Object { Write-Host '    path exclusion:' $_ }"

echo.
echo  Now double-click launch_model_studio.bat and the server
echo  should boot in a few seconds.
echo.
pause
exit /b 0
