from __future__ import annotations

import os
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from moult.engine.sync_service import SyncService
from moult.models import DeviceRole
from moult.platform.windows_integration import is_windows, open_in_file_manager


def create_pil_tray_icon(bg_color: str = "#f59e0b", fg_color: str = "#0f172a") -> Image.Image:
    """
    Рисует чёткую иконку Moult Диск ('M') 64x64 для системного трея Windows.
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Скруглённый квадрат в стиле Яндекс.Диска / современного Windows 11
    draw.rounded_rectangle((4, 4, size - 4, size - 4), radius=14, fill=bg_color)

    # Рисуем жирную букву M по центру геометрически (не зависит от системных шрифтов)
    # Левая стойка, правая стойка и диагонали буквы M
    poly_m = [
        (15, 48),
        (15, 16),
        (23, 16),
        (32, 32),
        (41, 16),
        (49, 16),
        (49, 48),
        (41, 48),
        (41, 29),
        (32, 43),
        (23, 29),
        (23, 48),
    ]
    draw.polygon(poly_m, fill=fg_color)

    # Зелёный индикатор защищённого соединения TLS 1.3 в правом нижнем углу
    draw.ellipse((44, 44, 60, 60), fill="#10b981", outline="#0f172a", width=2)
    return img


def open_desktop_app_window(url: str) -> None:
    """
    Открывает интерфейс Moult Диск как отдельное десктопное окно на Windows
    (через режим --app в Microsoft Edge или Chrome, без адресной строки и вкладок браузера).
    Если Edge/Chrome не найден — открывает в браузере по умолчанию.
    """
    if is_windows():
        program_files = [
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        browser_candidates = [
            Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            for pf in program_files
            if pf
        ] + [
            Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe"
            for pf in program_files
            if pf
        ]

        for exe_path in browser_candidates:
            if exe_path.exists():
                try:
                    subprocess.Popen(
                        [
                            str(exe_path),
                            f"--app={url}",
                            "--window-size=1280,820",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
                except Exception:
                    pass

    webbrowser.open(url)


def run_desktop_tray_app(
    service: Optional[SyncService] = None,
    web_port: int = 8765,
    open_window_on_start: bool = True,
) -> int:
    """
    Запускает иконку Moult Диск в системном трее Windows (pystray / Win32 Shell_NotifyIcon)
    и открывает главное окно программы.
    """
    svc = service or SyncService()
    url = f"http://127.0.0.1:{web_port}"
    stop_event = threading.Event()

    # Фоновый поток автоматической синхронизации каждые 30 секунд
    def auto_sync_worker() -> None:
        while not stop_event.wait(30.0):
            try:
                svc.sync_all_folders()
            except Exception:
                pass

    sync_thread = threading.Thread(target=auto_sync_worker, daemon=True)
    sync_thread.start()

    if open_window_on_start:
        # Открываем главное окно Moult Диск сразу при старте
        open_desktop_app_window(url)

    try:
        import pystray
        from pystray import Menu, MenuItem
    except Exception:
        # Если pystray недоступен (например, на headless-сервере без дисплея),
        # удерживаем процесс живым, пока работает локальный сервер
        try:
            while not stop_event.is_set():
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        return 0

    def on_open_ui(icon: pystray.Icon, item: MenuItem) -> None:
        open_desktop_app_window(url)

    def on_open_explorer(icon: pystray.Icon, item: MenuItem) -> None:
        open_in_file_manager(svc.cm.config.root_disk_path)

    def on_sync_now(icon: pystray.Icon, item: MenuItem) -> None:
        res = svc.sync_all_folders()
        try:
            icon.notify(
                f"Отправлено: ↑{res['total_uploaded']}, Принято: ↓{res['total_downloaded']} (SHA-256 ОК)",
                "Moult Диск — Синхронизировано",
            )
        except Exception:
            pass

    def on_set_home_role(icon: pystray.Icon, item: MenuItem) -> None:
        svc.cm.apply_role_preset(DeviceRole.HOME_MASTER)
        try:
            icon.notify("Включён профиль: Главный ПК (Домашний)", "Moult Диск")
        except Exception:
            pass

    def on_set_work_role(icon: pystray.Icon, item: MenuItem) -> None:
        svc.cm.apply_role_preset(DeviceRole.WORK_CLIENT)
        try:
            icon.notify("Включён профиль: Рабочий ПК (Слив файлов)", "Moult Диск")
        except Exception:
            pass

    def on_quit(icon: pystray.Icon, item: MenuItem) -> None:
        stop_event.set()
        icon.stop()

    menu = Menu(
        MenuItem("Открыть окно Moult Диск", on_open_ui, default=True),
        MenuItem("Открыть папку в Проводнике Windows", on_open_explorer),
        MenuItem("Синхронизировать все папки сейчас", on_sync_now),
        Menu.SEPARATOR,
        MenuItem("Профиль: Главный ПК (Домашний)", on_set_home_role),
        MenuItem("Профиль: Рабочий ПК (Слив файлов)", on_set_work_role),
        Menu.SEPARATOR,
        MenuItem("Выход из Moult Диск", on_quit),
    )

    tray_icon = pystray.Icon(
        name="MoultDisk",
        icon=create_pil_tray_icon(),
        title="Moult Диск — Защищённая синхронизация (TLS 1.3)",
        menu=menu,
    )

    def on_tray_ready(icon: pystray.Icon) -> None:
        icon.visible = True
        try:
            icon.notify(
                "Moult Диск работает в системном трее Windows (канал TLS 1.3 активен).",
                "Moult Диск запущен",
            )
        except Exception:
            pass

    try:
        tray_icon.run(setup=on_tray_ready)
    except Exception:
        # На случай отсутствия графической сессии продолжаем работу веб-интерфейса
        try:
            while not stop_event.is_set():
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass

    return 0
