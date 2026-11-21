@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
for %%V in (3.12 3.13 3.11 3.10 3.14) do (
  py -%%V -c "import venv,ensurepip" >nul 2>&1
  if not errorlevel 1 (
    py -%%V -u oneclick.py
    goto finished
  )
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -u oneclick.py
  goto finished
)
python -c "import sys,venv,ensurepip; sys.exit(sys.version_info < (3,10))" >nul 2>&1
if not errorlevel 1 (
  python -u oneclick.py
  goto finished
)
echo Please install Python 3.12 from python.org and open this file again.
:finished
pause
