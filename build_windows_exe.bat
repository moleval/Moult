@echo off
cd /d "%~dp0"
chcp 65001 >nul
title Сборка портативного MoultDisk.exe для Windows
echo ===============================================================
echo   Сборка портативного приложения MoultDisk.exe (PyInstaller)
echo ===============================================================

if not exist ".venv" (
    python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -r requirements-windows.txt

if not exist "bin\syncthing.exe" (
    python scripts\download_syncthing.py
)

pyinstaller --noconfirm --onedir --windowed ^
    --name "MoultDisk" ^
    --add-data "moult/web/static;moult/web/static" ^
    --add-binary "bin/syncthing.exe;bin" ^
    main.py

echo.
echo Готово! Скомпилированная программа для переноса на Главный и Рабочий ПК находится в папке:
echo dist\MoultDisk\MoultDisk.exe
pause
