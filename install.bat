@echo off
rem Cai dat Signer, ban Windows cua install.sh: tim Python >= 3.10, tao .venv, cai requirements.txt.
rem Chay lai bao nhieu lan cung duoc. Khong tu cai Python: thieu thi bao cach cai roi dung.
rem May test (khong phai may Signer): install.bat --dev de cai them requirements-test.txt
rem Python: SIGNER_PYTHON neu co, roi py -3 (Python Launcher), roi python
setlocal
cd /d "%~dp0"

set "CHECK=import sys; sys.exit(sys.version_info < (3, 10))"
set "PY="
if defined SIGNER_PYTHON "%SIGNER_PYTHON%" -c "%CHECK%" >nul 2>&1 && set "PY=%SIGNER_PYTHON%"
if not defined PY py -3 -c "%CHECK%" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "%CHECK%" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo Khong tim thay Python ^>= 3.10. Cai Python roi chay lai:
    echo   winget install Python.Python.3.12
    echo   hoac tai tu https://www.python.org/downloads/ ^(chon "Add python.exe to PATH"^)
    exit /b 1
)
echo Python: %PY%

rem .venv cu tao bang Python qua cu thi tao lai
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "%CHECK%" >nul 2>&1 || (echo Xoa .venv cu ^(Python ^< 3.10^) & rmdir /s /q .venv)
)
if not exist ".venv\Scripts\python.exe" (
    echo Tao .venv...
    %PY% -m venv .venv || exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip || exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.txt || exit /b 1
if "%~1"=="--dev" (
    ".venv\Scripts\python.exe" -m pip install -r requirements-test.txt || exit /b 1
)

echo.
echo Xong. signer.bat / start.bat tu dung .venv. Buoc tiep theo:
if not exist config.ini echo   copy config.example.ini config.ini   ^(roi xoa cac section [key:...] mau^)
echo   signer.bat   ^(them key^)
echo   start.bat    ^(chay Signer^)
exit /b 0
