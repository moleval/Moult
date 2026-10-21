from __future__ import annotations

import argparse
import threading
import uvicorn

from moult.desktop.tray_app import run_desktop_tray_app
from moult.engine.sync_service import SyncService
from moult.web.app import create_app


def start_background_web_server(service: SyncService, host: str, port: int) -> threading.Thread:
    app = create_app(service)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return thread


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Moult Диск — Защищённая синхронизация файлов для ПК (Windows) и Android"
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "web", "tray", "sync-once"],
        default="auto",
        help="Режим запуска: auto (Трей Windows + локальный сервер), web (только веб-интерфейс), tray (Qt6 трей), sync-once (однократная синхронизация)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Хост веб-интерфейса (по умолчанию 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Порт веб-интерфейса (по умолчанию 8000)")
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
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0

    # В режиме auto или tray запускаем фоновый API/Web-сервер и открываем системный трей Windows (PySide6)
    start_background_web_server(service, host="127.0.0.1", port=args.port)
    return run_desktop_tray_app(service=service, web_port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
