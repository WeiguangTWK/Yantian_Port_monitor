@echo off
rem ---------------------------------------------------------------
rem  Step 2: run one monitoring cycle
rem  Exit codes: 0=no change  10=changed  1=error  2=token  3=query failed
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
  echo.
  echo   Copy  ytmon\watchlist.example.json  to  ytmon\watchlist.json
  echo   and fill in the targets first.
  echo ===============================================================
  pause
  exit /b 2
)

echo ===============================================================
echo   Step 2 / one monitoring cycle
echo ===============================================================
echo.

"%HERE%ytmon\ytmon.exe" > "%HERE%run-log.txt" 2>&1
set RC=%ERRORLEVEL%

type "%HERE%run-log.txt"

echo.
echo ===============================================================
echo   exit code = %RC%
echo     0 = no change   10 = changed   1 = error
echo     2 = token invalid   3 = query failed
echo   Log saved to: run-log.txt
echo ===============================================================
echo.
pause
