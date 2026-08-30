@echo off
REM Zenith — the one command, for Windows.
REM   os              where things stand
REM   os help         everything you can type
setlocal
set "HERE=%~dp0"

if not exist "%HERE%.os\engine.py" (
  echo   x  Zenith's engine is missing: %HERE%.os\engine.py 1>&2
  echo      This file has to sit in the top of a Zenith folder to work. 1>&2
  exit /b 1
)

set "ZENITH_HOME=%HERE%"

where py >nul 2>&1 && goto :usepy
where python3 >nul 2>&1 && goto :usepython3
where python >nul 2>&1 && goto :usepython
goto :nopython

:usepy
py -3 "%HERE%.os\engine.py" %*
exit /b %errorlevel%

:usepython3
python3 "%HERE%.os\engine.py" %*
exit /b %errorlevel%

:usepython
python "%HERE%.os\engine.py" %*
exit /b %errorlevel%

:nopython
echo   x  Python is not installed. 1>&2
echo      Zenith needs Python 3.9 or newer. Get it from python.org 1>&2
echo      (tick "Add Python to PATH" in the installer.) 1>&2
exit /b 1
