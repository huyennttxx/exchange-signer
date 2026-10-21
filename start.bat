@echo off
rem Chay Signer, ban Windows cua start.sh.
rem Duong dan tinh tu thu muc chua file nay. Config khac: set SIGNER_CONFIG=D:\signer\config.json
rem Python: SIGNER_PYTHON neu co, roi .venv, roi python
setlocal
cd /d "%~dp0"

if defined SIGNER_PYTHON (set "PY=%SIGNER_PYTHON%") else if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
if not defined SIGNER_CONFIG set "SIGNER_CONFIG=config.json"

"%PY%" -m signer.server --config "%SIGNER_CONFIG%" %*
exit /b %ERRORLEVEL%
