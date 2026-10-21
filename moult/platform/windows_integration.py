from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import psutil

REGISTRY_RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REGISTRY_APP_KEY = "MoultDiskSync"


def is_windows() -> bool:
    return platform.system().lower().startswith("win")


def open_in_file_manager(target_path: str | Path) -> Dict[str, Any]:
    """
    Открывает папку или выделяет файл в Проводнике Windows (explorer.exe)
    либо в системном файловом менеджере ОС.
    """
    path = Path(target_path).resolve()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)

    try:
        if is_windows():
            os.startfile(str(path))  # type: ignore[attr-defined]
            return {"opened": True, "platform": "Windows Explorer", "path": str(path)}
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"opened": True, "platform": "macOS Finder", "path": str(path)}
        else:
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"opened": True, "platform": platform.system(), "path": str(path)}
    except Exception as exc:
        return {"opened": False, "platform": platform.system(), "path": str(path), "error": str(exc)}


def get_windows_autostart_status() -> bool:
    """
    Проверяет наличие записи автозапуска в реестре Windows (HKCU\\...\\Run).
    """
    if not is_windows():
        return False
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, REGISTRY_APP_KEY)
            return bool(val)
    except Exception:
        return False


def set_windows_autostart(enable: bool, script_path: str | None = None) -> Dict[str, Any]:
    """
    Включает или выключает автозапуск программы вместе с Windows (как у Яндекс.Диска).
    """
    if not is_windows():
        return {
            "supported_on_current_os": False,
            "enabled": enable,
            "note": "Запись в реестр HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run активна при запуске на Windows.",
        }

    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                exe = sys.executable
                if exe.lower().endswith("python.exe"):
                    pythonw = Path(exe).with_name("pythonw.exe")
                    if pythonw.exists():
                        exe = str(pythonw)
                entry_script = script_path or str(Path(sys.argv[0]).resolve())
                cmd = f'"{exe}" "{entry_script}" --tray'
                winreg.SetValueEx(key, REGISTRY_APP_KEY, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, REGISTRY_APP_KEY)
                except FileNotFoundError:
                    pass
        return {"supported_on_current_os": True, "enabled": enable}
    except Exception as exc:
        return {"supported_on_current_os": True, "enabled": False, "error": str(exc)}


def check_blocking_processes(executable_names: List[str]) -> List[str]:
    """
    Проверяет, запущены ли указанные процессы (например, программа домашней бухгалтерии),
    чтобы временно приостановить синхронизацию папки с базой данных и избежать повреждения SQLite/БД.
    """
    if not executable_names:
        return []
    targets = {name.strip().lower() for name in executable_names if name.strip()}
    running_matches: List[str] = []
    for proc in psutil.process_iter(["name"]):
        try:
            proc_name = (proc.info.get("name") or "").lower()
            if proc_name in targets:
                running_matches.append(proc.info.get("name") or proc_name)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sorted(set(running_matches))


def get_disk_space_info(path: str | Path) -> Dict[str, Any]:
    """
    Возвращает статистику свободного и занятого места на диске (для виджета в стиле Яндекс.Диска).
    """
    target = Path(path)
    if not target.exists():
        target = Path.cwd()
    usage = shutil.disk_usage(str(target))
    used_percent = round((usage.used / usage.total) * 100, 1) if usage.total else 0.0
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_gb": round(usage.total / (1024**3), 2),
        "used_gb": round(usage.used / (1024**3), 2),
        "free_gb": round(usage.free / (1024**3), 2),
        "used_percent": used_percent,
    }
