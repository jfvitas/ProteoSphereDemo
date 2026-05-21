@echo off
REM ============================================================
REM  ProteoSphere Model Studio v2 -- launcher (project root)
REM ------------------------------------------------------------
REM  Lives at the repo root for easy double-click access.
REM  The v2 code currently lives in a Claude Code worktree under
REM  .claude\worktrees\wonderful-gates -- this script cds in
REM  there and starts the slim v2 server (no torch at boot).
REM
REM  Usage:
REM    launch_model_studio.bat              -> port 8765
REM    launch_model_studio.bat 9000         -> port 9000
REM    launch_model_studio.bat 8765 nogui   -> don't auto-open browser
REM ============================================================

setlocal EnableExtensions EnableDelayedExpansion

REM ---- locate the staged repo root ---------------------------
REM We resolve this relative to %~dp0 (this script's directory)
REM so moving the project to a different drive / folder still works.
set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"
set "WORKTREE=%REPO_ROOT%"

REM ---- demo warehouse + ingest catalog wiring ----------------
REM The slim server reads its v2 ingest catalog from
REM PROTEOSPHERE_V2_INGEST_ROOT/catalog/v2.duckdb. The bundled
REM demo warehouse provides exactly that. We honour any user-set
REM env var first (so power users can point at their own catalog),
REM otherwise we wire the bundled one.
if not defined PROTEOSPHERE_V2_INGEST_ROOT if exist "%REPO_ROOT%\demo_warehouse\catalog\v2.duckdb" (
    set "PROTEOSPHERE_V2_INGEST_ROOT=%REPO_ROOT%\demo_warehouse"
)
REM The ESM-2 embedding cache lives next to it.
if not defined PROTEOSPHERE_V2_EMBEDDINGS if exist "%REPO_ROOT%\demo_warehouse\embeddings" (
    set "PROTEOSPHERE_V2_EMBEDDINGS=%REPO_ROOT%\demo_warehouse\embeddings"
)

if not exist "%WORKTREE%\api\model_studio\server_v2.py" (
    echo.
    echo  ERROR: cannot find the model studio server entry point at:
    echo    %WORKTREE%\api\model_studio\server_v2.py
    echo.
    echo  The launcher must live in the same directory as the api\
    echo  package. If you've moved files, run from the repo root.
    echo.
    pause
    exit /b 1
)

REM ---- arg parsing -------------------------------------------
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8765"
set "GUI_FLAG=%~2"

REM ---- kill any prior server_v2 process on this port ---------
REM Windows lets two listeners share a port without
REM SO_EXCLUSIVEADDRUSE, so a second double-click of this
REM launcher leaves us with two python.exes round-robin'ing
REM incoming requests between them. That makes "I restarted
REM and it still doesn't work" a routine failure mode whenever
REM code changes ship. We scan for processes already bound to
REM the target port and terminate them BEFORE starting the new
REM one. (netstat -ano + taskkill is robust across every
REM supported Windows since 7.)
echo  Checking for prior listeners on port %PORT%...
set "KILLED_ANY=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    REM Skip the PID 0 entries netstat sometimes prints for TIME_WAIT.
    if not "%%P"=="0" (
        echo    killing python.exe PID %%P
        taskkill /F /PID %%P >nul 2>&1
        set "KILLED_ANY=1"
    )
)
if "!KILLED_ANY!"=="1" (
    REM Give the OS a moment to release the socket so the new
    REM bind doesn't race the dying process.
    timeout /t 1 /nobreak >nul
    echo    OK: prior listener^(s^) terminated.
) else (
    echo    OK: no prior listener on port %PORT%.
)

REM ---- locate python -----------------------------------------
REM Prefer the user's system Python 3.12 (per machine_specs.md).
REM Fall back to PATH if the explicit path isn't there.
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo  ============================================================
echo   ProteoSphere Model Studio v2
echo  ============================================================
echo   repo root : %REPO_ROOT%
echo   worktree  : %WORKTREE%
echo   python    : %PY%
echo   port      : %PORT%
echo   GUI URL   : http://127.0.0.1:%PORT%/v2/
echo  ============================================================
echo.

REM ---- dependency check (FILESYSTEM-based, no python.exe spawn) -
REM We DO NOT spawn `python -c "import duckdb"` as a dependency probe
REM because Windows Defender's real-time scan of python.exe + large
REM .pyd files (torch especially) can hang the probe for minutes on
REM machines where the file-locking heuristic has been triggered.
REM Instead we inspect site-packages on disk directly.
REM
REM Derive site-packages from the python.exe path:
REM   <python_dir>/python.exe -> <python_dir>/Lib/site-packages
REM This is the standard layout for the python.org installer. Conda
REM environments use the same layout. Virtualenvs use Scripts/ so
REM we check both.
for %%F in ("%PY%") do set "PY_DIR=%%~dpF"
if "%PY_DIR:~-1%"=="\" set "PY_DIR=%PY_DIR:~0,-1%"
set "SITE_PKG=%PY_DIR%\Lib\site-packages"
if not exist "%SITE_PKG%" set "SITE_PKG=%PY_DIR%\..\Lib\site-packages"

echo  Checking Python dependencies in %SITE_PKG%...
set "MISSING="
if not exist "%SITE_PKG%\duckdb"  set "MISSING=!MISSING! duckdb"
if not exist "%SITE_PKG%\pyarrow" set "MISSING=!MISSING! pyarrow"
if not exist "%SITE_PKG%\torch"   set "MISSING=!MISSING! torch"
if "!MISSING!"=="" goto deps_ok
echo   Missing packages:!MISSING!

echo.
echo  -----------------------------------------------------------
echo   MISSING DEPENDENCIES
echo  -----------------------------------------------------------
echo.
echo   The Model Studio needs duckdb + pyarrow + torch installed
echo   in your active Python environment. They are not yet present.
echo.
echo   Would you like me to install them now?
echo   ^(takes ~3-4 minutes, no admin rights needed^)
echo.
set "INSTALL_REPLY=Y"
set /p "INSTALL_REPLY=Install dependencies? [Y/n] "
if /I "!INSTALL_REPLY!"=="n"  goto deps_user_skipped
if /I "!INSTALL_REPLY!"=="no" goto deps_user_skipped

echo.
echo   Running: %PY% -m pip install --upgrade pip
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto deps_install_failed
echo.
echo   Running: %PY% -m pip install -r requirements.txt
"%PY%" -m pip install -r "%REPO_ROOT%\requirements.txt"
if errorlevel 1 goto deps_install_failed
echo.
echo   Dependencies installed. Continuing with launch...
echo.
goto deps_ok

:deps_install_failed
echo.
echo   pip install failed. Common fixes:
echo     * make sure you're online
echo     * try the --user flag:  %PY% -m pip install -r "%REPO_ROOT%\requirements.txt" --user
echo.
echo  Press any key to close...
pause >nul
exit /b 4

:deps_user_skipped
echo.
echo   Skipped. To install manually:
echo     %PY% -m pip install -r "%REPO_ROOT%\requirements.txt"
echo.
echo  Press any key to close...
pause >nul
exit /b 3

:deps_ok

REM ---- spawn the browser-opener watchdog ----------------------
REM Instead of a fixed 2-second delay (which races torch's cold
REM import, especially when Defender is scanning .pyd files), we
REM spawn a background poller that waits for the port to actually
REM be in LISTENING state before opening the browser. Polls every
REM 1 second, gives up + prints the URL if not bound within 120s.
REM
REM Inline the polling script via cmd /v:on /c so we can use
REM delayed expansion to update a counter inside the loop.
if /I not "%GUI_FLAG%"=="nogui" (
    start "ProteoSphere browser-opener" /MIN cmd /v:on /c "set N=0 & :loop & timeout /t 1 /nobreak ^>nul & netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING" ^>nul & if not errorlevel 1 (start "" http://127.0.0.1:%PORT%/v2/ & exit /b 0) & set /a N+=1 & if !N! LSS 120 goto loop & echo Server did not bind port %PORT% within 120 seconds. Open http://127.0.0.1:%PORT%/v2/ manually once the server log says 'Listening on'. & exit /b 1"
)

echo.
echo  Starting server (first launch can take 30-60 seconds while
echo  torch loads its native libraries)...
echo  The browser will open automatically once the server is ready.
echo.
echo  If it takes more than 60 seconds, your antivirus is probably
echo  scanning torch's .pyd files on first load. Run
echo  setup_windows_defender.bat (one-time, as admin) to add the
echo  needed exclusions.
echo.

REM ---- launch the slim v2 server from inside the worktree ----
REM This entry point is deliberately minimal:
REM    * static GUI assets at /v2/*
REM    * API routes at /api/v2/*
REM Torch loads lazily on the first training launch from the
REM Pipeline screen; the server itself binds the port in <2s
REM on a non-Defender-blocked machine.
pushd "%WORKTREE%"
"%PY%" -X utf8 -m api.model_studio.server_v2 --port %PORT%
set "RC=%ERRORLEVEL%"
popd

if not "%RC%"=="0" (
    echo.
    echo  Server exited with code %RC%.
    echo.
    echo  Most common cause: torch import hung due to Windows
    echo  Defender. Try running setup_windows_defender.bat once
    echo  as an administrator, then re-launch.
    echo.
    echo  Press any key to close...
    pause >nul
) else (
    echo.
    echo  Server stopped cleanly. Press any key to close...
    pause >nul
)

endlocal
