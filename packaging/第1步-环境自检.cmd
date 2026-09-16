@echo off
rem ---------------------------------------------------------------
rem  Step 1: environment self-check
rem
rem  NOTE: keep this file ASCII-only. Chinese in a .cmd is unreliable
rem  on a Chinese Windows console (the file is read as bytes in the
rem  console codepage), so all Chinese explanation lives in the .txt
rem  field card at the package root instead.
rem
rem  Two encoding details, both measured on a real machine:
rem    * chcp 65001 is for the "type" below -- it makes the console render
rem      the UTF-8 log correctly. It does NOT affect the redirect encoding.
rem    * PYTHONIOENCODING is cleared on purpose. When it is set, ytmon's
rem      console guard assumes the user picked the encoding and leaves it
rem      alone -- but a frozen exe ignores that variable, so the log would
rem      land as cp936 instead of UTF-8.
rem ---------------------------------------------------------------
chcp 65001 >nul
setlocal
set PYTHONIOENCODING=
set HERE=%~dp0

cd /d "%HERE%ytmon"

echo ===============================================================
echo   Step 1 / environment self-check
echo ===============================================================
echo.
echo   (checking, this may take 1-2 minutes...)
echo.

"%HERE%ytmon\ytmon-check.exe" > "%HERE%check-log.txt" 2>&1
set RC=%ERRORLEVEL%

type "%HERE%check-log.txt"

echo.
echo ===============================================================
if "%RC%"=="0" (
  echo   RESULT: all layers passed  ^(exit code 0^)
) else (
  echo   RESULT: something failed  ^(exit code %RC%^)
  echo   Send check-log.txt back.
)
echo   Log saved to: check-log.txt
echo ===============================================================
echo.
pause
