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

".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q pyinstaller || goto :fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm jilo.spec || goto :fail

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
  "%ISCC%" installer\jilo.iss || goto :fail
  echo.
  echo DONE: installer is in the "dist" folder: Jilo-Setup-1.0.exe
) else (
  powershell -NoProfile -Command "Compress-Archive -Force -Path dist\Jilo -DestinationPath dist\Jilo-Windows-portable.zip"
  echo.
  echo DONE: portable program is in "dist\Jilo" ^(Jilo.exe^) and dist\Jilo-Windows-portable.zip
  echo For a real installer, install Inno Setup 6 ^(jrsoftware.org^) and run this file again.
)
pause
exit /b

:nopy
echo Python 3.10-3.13 not found. Install Python 3.12 from https://www.python.org/downloads/
pause
exit /b

:fail
echo.
echo Build failed. Take a screenshot of this window.
pause
exit /b
