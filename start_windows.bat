@echo off
cd /d "%~dp0"
chcp 65001 >nul
title Moult Диск — Защищённая синхронизация ПК (Windows)
echo ====================================================================
echo   Moult Диск — Запуск клиента синхронизации (D:\Moult)
echo ====================================================================

where python >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Python не найден в PATH! Установите Python 3.10+ с галочкой "Add Python to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Создание виртуального окружения Python в D:\Moult\.venv...
    python -m venv .venv
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать виртуальное окружение .venv
        pause
        exit /b 1
    )
)

echo [2/4] Установка и проверка компонентов (FastAPI, Cryptography, Tray)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ОШИБКА] Не удалось установить зависимости из requirements.txt
    pause
    exit /b 1
)

if not exist "bin\syncthing.exe" (
    echo [3/4] Загрузка проверенного бинарника syncthing.exe с проверкой SHA-256...
    ".venv\Scripts\python.exe" scripts\download_syncthing.py
)

echo [4/4] Запуск Moult Диск (Десктопное окно + Иконка в системном трее Windows)...
echo.
echo  - Главное окно программы открывается автоматически (http://127.0.0.1:8765)
echo  - Иконка "M" добавлена в системный трей Windows (около часов; в Windows 11
echo    новые иконки могут находиться под стрелочкой [^] рядом с часами).
echo.

start "" ".venv\Scripts\pythonw.exe" main.py --mode auto --port 8765
timeout /t 4 >nul
exit
