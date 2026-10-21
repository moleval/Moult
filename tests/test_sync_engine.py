from __future__ import annotations

import time
from pathlib import Path

from moult.config import ConfigManager
from moult.engine.sync_service import SyncService
from moult.models import DeviceRole, SyncMode


def _create_isolated_service(tmp_path: Path) -> SyncService:
    cm = ConfigManager(data_dir=tmp_path / "data", storage_root=tmp_path / "storage")
    return SyncService(config_manager=cm)


def test_bidirectional_sync_and_version_restore(tmp_path: Path) -> None:
    svc = _create_isolated_service(tmp_path)
    folder = next(f for f in svc.cm.config.folders if f.id == "common-docs")
    assert folder.sync_mode == SyncMode.SEND_RECEIVE

    local_dir = Path(folder.path)
    remote_dir = Path(folder.paired_local_path)

    # 1. Создаём файл на локальном ПК и синхронизируем
    f_local = local_dir / "Контракт.txt"
    f_local.write_text("Версия 1: черновик контракта", encoding="utf-8")

    res1 = svc.sync_folder("common-docs")
    assert res1["uploaded"] >= 1
    assert (remote_dir / "Контракт.txt").read_text(encoding="utf-8") == "Версия 1: черновик контракта"

    # 2. Обновляем файл на втором ПК (более свежий mtime) — старая версия должна попасть в Корзину версий
    time.sleep(1.6)
    (remote_dir / "Контракт.txt").write_text("Версия 2: финальный подписанный контракт", encoding="utf-8")

    res2 = svc.sync_folder("common-docs")
    assert res2["downloaded"] == 1
    assert f_local.read_text(encoding="utf-8") == "Версия 2: финальный подписанный контракт"

    # Проверяем, что Версия 1 сохранена в Корзине и её можно восстановить
    assert len(svc.versions) >= 1
    v_entry = next(v for v in svc.versions if v.file_name == "Контракт.txt")
    restore_res = svc.restore_version(v_entry.version_id)
    assert restore_res["restored"] is True
    assert f_local.read_text(encoding="utf-8") == "Версия 1: черновик контракта"


def test_work_pc_send_only_and_home_pc_receive_only(tmp_path: Path) -> None:
    svc = _create_isolated_service(tmp_path)

    # Переключаем ПК в режим "Рабочий ПК (Слив файлов)"
    svc.cm.apply_role_preset(DeviceRole.WORK_CLIENT)
    work_folder = next(f for f in svc.cm.config.folders if f.id == "work-dump")
    assert work_folder.sync_mode == SyncMode.SEND_ONLY

    local_work = Path(work_folder.path)
    remote_home = Path(work_folder.paired_local_path)

    # Создаём рабочий файл на Рабочем ПК
    (local_work / "Смета_проекта.xlsx").write_bytes(b"WORK_DATA_2026")
    # Игнорируемый временный файл Excel не должен копироваться
    (local_work / "~$Смета_проекта.xlsx").write_bytes(b"TEMP_LOCK")

    res = svc.sync_folder("work-dump")
    assert res["uploaded"] == 1
    assert (remote_home / "Смета_проекта.xlsx").exists()
    assert not (remote_home / "~$Смета_проекта.xlsx").exists()
