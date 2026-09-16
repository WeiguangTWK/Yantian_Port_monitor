@echo off
rem ---------------------------------------------------------------
rem  Step 3: test every alert channel
rem  Sends one test message through each enabled channel.
rem  (ASCII only - see the note in the step-1 .cmd)
rem ---------------------------------------------------------------
chcp 65001 >nul
setlocal
set PYTHONIOENCODING=
set HERE=%~dp0

cd /d "%HERE%ytmon"

if not exist "%HERE%ytmon\watchlist.json" (
  echo ===============================================================
  echo   [ERROR] ytmon\watchlist.json not found.
  echo   Fill in the notify section first, then run this again.
  echo ===============================================================
  pause
  exit /b 2
)

echo ===============================================================
echo   Step 3 / alert channel self-test
echo ===============================================================
echo.

"%HERE%ytmon\ytmon.exe" --test-alert > "%HERE%alert-log.txt" 2>&1
set RC=%ERRORLEVEL%

type "%HERE%alert-log.txt"

echo.
echo ===============================================================
echo   exit code = %RC%   (0 = all channels OK, 1 = at least one failed)
echo   Log saved to: alert-log.txt
echo ===============================================================
echo.
pause
