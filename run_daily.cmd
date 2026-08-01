@echo off
REM ---------------------------------------------------------------------------
REM  What Task Scheduler runs. Registered as "SPB Daily Harvest", 03:00 daily.
REM
REM  A wrapper rather than pointing the task straight at python.exe, for three
REM  reasons that each cost something to learn:
REM
REM    * Task Scheduler throws away stdout. Without the redirect below, a run that
REM      failed at 3am leaves nothing to read but an exit code, and the whole point
REM      of run_daily.py's step-by-step output is to say WHICH step failed.
REM    * The interpreter is pinned. The task inherits a service-ish PATH, and the
REM      Windows Store python.exe shim on this machine resolves ahead of the real
REM      one from some contexts.
REM    * Nested quoting in `schtasks /TR` is its own small nightmare; a .cmd file is
REM      something a human can also just double-click.
REM
REM  Deploying is deliberately NOT here. run_daily.py refreshes the data and stops,
REM  so a bad harvest can never publish itself over a good snapshot. To publish:
REM      powershell -File D:\Coleen\app\deploy.ps1
REM ---------------------------------------------------------------------------

set "APP=%~dp0"
set "PY=C:\Python313\python.exe"
set "LOG=%APP%_daily.log"

cd /d "%APP%"

echo. >> "%LOG%"
echo ====================================================================== >> "%LOG%"
echo   SPB daily run started %DATE% %TIME% >> "%LOG%"
echo ====================================================================== >> "%LOG%"

"%PY%" -X utf8 "%APP%run_daily.py" >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%

if %RC% NEQ 0 (
  echo [FAILED] exit code %RC% at %DATE% %TIME% >> "%LOG%"
) else (
  echo [OK] finished %DATE% %TIME% >> "%LOG%"
)
exit /b %RC%
