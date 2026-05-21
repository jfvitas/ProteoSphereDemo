@echo off
REM ============================================================
REM  ProteoSphere Model Studio v2 — launcher (project root)
REM ------------------------------------------------------------
REM  Lives at the repo root for easy double-click access.
REM  The v2 code currently lives in a Claude Code worktree under
REM  .claude\worktrees\wonderful-gates — this script cds in
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

REM ---- dependency check ----------------------------------------
REM On a fresh `git clone` (or unzipped GitHub download), nothing is
REM installed yet. We probe for duckdb + pyarrow + torch and surface
REM a clear "run pip install first" message rather than letting
REM Python crash silently after the banner with ModuleNotFoundError.
echo  Checking Python dependencies...
"%PY%" -X utf8 -c "import duckdb, pyarrow" 2>nul
if errorlevel 1 (
    echo.
    echo  -----------------------------------------------------------
    echo  MISSING DEPENDENCIES
    echo  -----------------------------------------------------------
    echo.
    echo  The Model Studio needs duckdb + pyarrow + torch installed
    echo  in your active Python environment. They are not yet present.
    echo.
    echo  Would you like me to install them now? ^(takes ~3-4 minutes
    echo  on a normal connection, no admin rights needed^)
    echo.
    set "INSTALL_REPLY="
    set /p INSTALL_REPLY=Install dependencies? [Y/n] ^>
    if /I not "!INSTALL_REPLY!"=="n" (
        echo.
        echo  Running: pip install -r requirements.txt
        "%PY%" -m pip install --upgrade pip
        "%PY%" -m pip install -r "%REPO_ROOT%\requirements.txt"
        if errorlevel 1 (
            echo.
            echo  pip install failed. See the messages above. Common fixes:
            echo    * make sure you're online
            echo    * try: %PY% -m pip install --upgrade pip
            echo    * try: %PY% -m pip install -r "%REPO_ROOT%\requirements.txt" --user
            echo.
            pause
            exit /b 4
        )
        echo.
        echo  Dependencies installed. Continuing with launch...
        echo.
    ) else (
        echo.
        echo  Aborted. To install manually, run:
        echo    %PY% -m pip install -r "%REPO_ROOT%\requirements.txt"
        echo.
        pause
        exit /b 3
    )
)

REM ---- open the browser shortly after the server starts ------
REM We schedule the open in a detached cmd so the server's
REM stdout stays in the foreground window.
if /I not "%GUI_FLAG%"=="nogui" (
    start "" /MIN cmd /c "timeout /t 2 /nobreak >nul && start "" http://127.0.0.1:%PORT%/v2/"
)

REM ---- launch the slim v2 server from inside the worktree ----
REM This entry point is deliberately minimal:
REM    * static GUI assets at /v2/*
REM    * API routes at /api/v2/*
REM No torch / sklearn at boot — those load lazily on the first
REM training launch from the Pipeline screen.
pushd "%WORKTREE%"
"%PY%" -X utf8 -m api.model_studio.server_v2 --port %PORT%
set "RC=%ERRORLEVEL%"
popd

if not "%RC%"=="0" (
    echo.
    echo  Server exited with code %RC%.
    echo  Press any key to close...
    pause >nul
)

endlocal
