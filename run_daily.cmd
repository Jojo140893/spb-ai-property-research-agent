@echo off
REM ---------------------------------------------------------------------------
REM  What Task Scheduler runs. Registered as "SPB Daily Harvest", 03:00 daily.
REM
REM      run_daily.cmd                harvest, then publish if it succeeded
REM      run_daily.cmd no-deploy      refresh the data only
REM      run_daily.cmd deploy-only    republish the current data, no harvest
REM
REM  A wrapper rather than pointing the task straight at python.exe, for three
REM  reasons that each cost something to learn:
REM
REM    * Task Scheduler throws away stdout. Without the log below, a run that failed
REM      at 3am leaves nothing to read but an exit code, and the whole point of
REM      run_daily.py's step-by-step output is to say WHICH step failed.
REM    * The interpreter is pinned. The task inherits a service-ish PATH, and the
REM      Windows Store python.exe shim on this machine resolves ahead of the real one
REM      from some contexts.
REM    * Nested quoting in `schtasks /TR` is its own small nightmare; a .cmd file is
REM      something a human can also just double-click.
REM
REM  THIS FILE MUST KEEP CRLF LINE ENDINGS. With bare LF, cmd.exe mis-parses labels
REM  and goto blocks and starts executing these REM lines as commands. .gitattributes
REM  pins it so a checkout cannot silently reintroduce that.
REM
REM  PUBLISHING IS GATED ON THE HARVEST SUCCEEDING. run_daily.py deliberately stops
REM  after refreshing the data, and deploy.ps1 is a separate step, precisely so a bad
REM  harvest can never overwrite a good snapshot on the live site. Only the SCHEDULING
REM  of the two is joined here — the deploy is reached only on exit code 0, so the
REM  safety rule itself is not weakened.
REM ---------------------------------------------------------------------------

setlocal
set "APP=%~dp0"
set "PY=C:\Python313\python.exe"
set "LOG=%APP%_daily.log"

REM The whole run writes through ONE redirect, opened once here. Redirecting each
REM echo separately raced with the child process that was still holding the file:
REM the deploy worked but its "[OK] published" line was silently dropped with a
REM "process cannot access the file" on the console nobody reads at 3am. Child
REM processes inherit this handle, so their output lands in the log too.
call :main %* >> "%LOG%" 2>&1
endlocal & exit /b %ERRORLEVEL%


:main
set "MODE=%~1"
echo.
echo ======================================================================
echo   SPB daily run started %DATE% %TIME%   mode=%MODE%
echo ======================================================================

cd /d "%APP%"
set RC=0
if /I "%MODE%"=="deploy-only" goto :deploy

"%PY%" -X utf8 "%APP%run_daily.py"
set RC=%ERRORLEVEL%

if %RC% NEQ 0 (
  echo [FAILED] harvest exited %RC% at %DATE% %TIME% - NOT publishing.
  echo          The live site keeps the last good snapshot.
  goto :done
)

if /I "%MODE%"=="no-deploy" (
  echo [OK] data refreshed %DATE% %TIME%; publishing skipped ^(no-deploy^).
  goto :done
)

:deploy
echo.
echo ----------------------------------------------------------------------
echo   Publishing to Vercel %DATE% %TIME%
echo ----------------------------------------------------------------------
REM -ExecutionPolicy Bypass because a scheduled, non-interactive shell does not
REM inherit the signing policy an interactive one does, and this is our own script.
powershell -NoProfile -ExecutionPolicy Bypass -File "%APP%deploy.ps1"
set RC=%ERRORLEVEL%
if %RC% NEQ 0 (
  echo [FAILED] deploy exited %RC% at %DATE% %TIME%
  echo          Data IS refreshed locally; only publishing failed.
  echo          Retry with: run_daily.cmd deploy-only
) else (
  echo [OK] published %DATE% %TIME%
)

:done
if %RC% EQU 0 (
  echo [OK] finished %DATE% %TIME%
) else (
  echo [FAILED] finished with %RC% at %DATE% %TIME%
)
exit /b %RC%
