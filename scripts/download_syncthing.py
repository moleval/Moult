from __future__ import annotations

import hashlib
import io
import platform
import zipfile
from pathlib import Path

import requests

GITHUB_LATEST_API = "https://api.github.com/repos/syncthing/syncthing/releases/latest"


def download_verified_syncthing(target_dir: Path | None = None) -> Path | None:
    """
    Скачивает официальный релиз syncthing.exe (для Windows x64) с GitHub
    и проверяет контрольную сумму SHA-256 по файлу sha256sum.txt.asc.
    """
    root_dir = Path(__file__).resolve().parent.parent
    out_dir = (target_dir or (root_dir / "bin")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    existing_exe = out_dir / "syncthing.exe"
    if existing_exe.exists():
        print(f"[Moult Security] Движок уже установлен: {existing_exe}")
        return existing_exe

    try:
        resp = requests.get(GITHUB_LATEST_API, timeout=15)
        resp.raise_for_status()
        release = resp.json()
        tag = release["tag_name"]

        sys_name = platform.system().lower()
        asset_prefix = (
            "syncthing-windows-amd64" if sys_name.startswith("win") else "syncthing-linux-amd64"
        )

        assets = release.get("assets", [])
        archive_asset = next(
            (a for a in assets if a["name"].startswith(asset_prefix) and a["name"].endswith(".zip")),
            None,
        )
        sums_asset = next((a for a in assets if a["name"] == "sha256sum.txt.asc"), None)

        if not archive_asset:
            print(f"[Moult] Архив {asset_prefix}.zip не требуется для текущей ОС.")
            return None

        print(f"[Moult Security] Скачивание {archive_asset['name']} ({tag})...")
        arch_resp = requests.get(archive_asset["browser_download_url"], timeout=90)
        arch_resp.raise_for_status()
        archive_bytes = arch_resp.content
        actual_sha256 = hashlib.sha256(archive_bytes).hexdigest()

        if sums_asset:
            sums_resp = requests.get(sums_asset["browser_download_url"], timeout=15)
            sums_resp.raise_for_status()
            expected_sha256 = None
            for line in sums_resp.text.splitlines():
                if archive_asset["name"] in line:
                    expected_sha256 = line.strip().split()[0]
                    break
            if expected_sha256 and expected_sha256.lower() != actual_sha256.lower():
                raise ValueError(
                    f"КРИТИЧЕСКАЯ ОШИБКА БЕЗОПАСНОСТИ: SHA-256 архива не совпадает! "
                    f"Ожидался {expected_sha256}, получен {actual_sha256}"
                )
            print(f"[Moult Security] Подпись SHA-256 подтверждена: {actual_sha256}")

        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            for member in zf.namelist():
                if member.endswith("syncthing.exe") or member.endswith("/syncthing"):
                    exe_name = "syncthing.exe" if member.endswith(".exe") else "syncthing"
                    dst_file = out_dir / exe_name
                    dst_file.write_bytes(zf.read(member))
                    print(f"[Moult Security] Установлен бинарный движок: {dst_file}")
                    return dst_file
    except Exception as exc:
        print(
            f"[Moult] Примечание: не удалось скачать syncthing.exe автоматически ({exc}). "
            "Запускаем встроенное ядро Moult Sync."
        )
    return None


if __name__ == "__main__":
    download_verified_syncthing()
