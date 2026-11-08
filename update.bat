@echo off
rem Cap nhat ma nguon Signer bang git pull (chi fast-forward), ban Windows cua update.sh. Khong cai / cap nhat Python hay thu vien.
rem Khong dong toi config.ini / keystore.json (khong nam trong git). Signer dang chay thi phai khoi dong lai sau khi cap nhat.
setlocal
cd /d "%~dp0"

rem Ca khoi ( ) duoc cmd doc het truoc khi chay, nen git pull co sua chinh file nay cung khong lam hong phan con lai.
(
    where git >nul 2>&1 || (echo Chua co git. Cai git: winget install Git.Git & exit /b 1)
    git rev-parse --is-inside-work-tree >nul 2>&1 || (echo Thu muc nay khong phai git clone, khong cap nhat bang git pull duoc. & exit /b 1)

    echo Cap nhat ma nguon...
    git pull --ff-only || (echo git pull loi: co sua doi local hoac nhanh da re nhanh. Xem git status, sua roi chay lai. & exit /b 1)

    echo.
    echo Xong. Signer dang chay thi khoi dong lai: start.bat
    exit /b 0
)
