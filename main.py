from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

# На Windows в режиме pythonw.exe стандартные потоки stdout/stderr равны None.
# Перенаправляем их в лог-файл в корне проекта (D:\Moult\moult.log), чтобы библиотеки (uvicorn и др.) не падали.
ROOT_DIR = Path(__file__).resolve().parent
LOG_FILE_PATH = ROOT_DIR / "moult.log"
if sys.stdout is None or sys.stderr is None:
    log_handle = open(LOG_FILE_PATH, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = log_handle
    if sys.stderr is None:
        sys.stderr = log_handle

import uvicorn

from moult.desktop.tray_app import run_desktop_tray_app
from moult.engine.sync_service import SyncService
from moult.web.app import create_app


def _wait_for_port(host: str, port: int, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    check_host = "127.0.0.1" if host == "0.0.0.0" else host
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            if sock.connect_ex((check_host, port)) == 0:
                return True
        time.sleep(0.15)
    return False


def start_background_web_server(service: SyncService, host: str, port: int) -> threading.Thread:
    app = create_app(service)
    # log_config=None критически важен для запуска через pythonw.exe на Windows
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_for_port(host, port)
    return thread


def _show_windows_error_box(title: str, message: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Moult Диск — Защищённая синхронизация файлов для ПК (Windows) и Android"
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "web", "tray", "sync-once"],
        default="auto",
        help="Режим запуска: auto (Трей Windows + десктопное окно), web (только веб-сервер), tray (трей без открытия окна), sync-once (однократная синхронизация)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Хост веб-интерфейса")
    parser.add_argument("--port", type=int, default=8765, help="Порт локального сервера (по умолчанию 8765)")
    args = parser.parse_args()

    service = SyncService()

    if args.mode == "sync-once":
        res = service.sync_all_folders()
        print(
            f"[Moult Диск] Синхронизация завершена: отправлено ↑{res['total_uploaded']}, "
            f"принято ↓{res['total_downloaded']}, конфликтов: {res['total_conflicts']}"
        )
        return 0

    if args.mode == "web":
        app = create_app(service)
        config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info", log_config=None)
        server = uvicorn.Server(config)
        server.run()
        return 0

    # В режиме auto или tray запускаем фоновый локальный сервер (127.0.0.1) и иконку в системном трее Windows
    start_background_web_server(service, host="127.0.0.1", port=args.port)
    open_win = args.mode == "auto"
    return run_desktop_tray_app(service=service, web_port=args.port, open_window_on_start=open_win)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        err_text = traceback.format_exc()
        err_file = ROOT_DIR / "moult_error.log"
        try:
            err_file.write_text(err_text, encoding="utf-8")
        except Exception:
            pass
        _show_windows_error_box(
            "Ошибка запуска Moult Диск",
            f"Произошла ошибка при запуске:\n{exc}\n\nПодробности записаны в {err_file}",
        )
        raise SystemExit(1)
