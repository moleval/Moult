@echo off
cd /d "%~dp0"
chcp 65001 >nul
title Moult Диск — Защищённая синхронизация ПК (Windows)
echo ===============================================================
echo   Moult Диск — Запуск клиента синхронизации (Главный / Рабочий ПК)
echo ===============================================================

if not exist ".venv" (
    echo [1/3] Создание виртуального окружения Python...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo [2/3] Проверка зависимостей (PySide6, FastAPI, Cryptography)...
pip install -q -r requirements-windows.txt

if not exist "bin\syncthing.exe" (
    echo [2.5/3] Загрузка проверенного бинарника syncthing.exe с проверкой SHA-256...
    python scripts\download_syncthing.py
)

echo [3/3] Запуск Moult Диск (Системный трей Windows + Панель управления)...
start "" pythonw main.py --mode auto --port 8765
exit
