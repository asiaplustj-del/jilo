@echo off
setlocal
cd /d "%~dp0"

rem ---- choose Python (3.12 preferred) ----
set "PY="
for %%V in (3.12 3.11 3.13 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; assert sys.version_info>=(3,10)" >nul 2>nul && set "PY=python"
)
if not defined PY goto :nopy

if not exist ".venv\Scripts\python.exe" (
  echo Creating environment, first run takes a few minutes...
  %PY% -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail

start "" ".venv\Scripts\pythonw.exe" run_jilo.py
exit /b

:nopy
echo Python 3.10-3.13 not found. Install Python 3.12 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH".
pause
exit /b

:fail
echo.
echo Something went wrong. Take a screenshot of this window.
pause
exit /b
