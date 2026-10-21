from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from moult.config import ConfigManager
from moult.desktop.tray_app import create_pil_tray_icon
from moult.engine.sync_service import SyncService
from moult.web.app import create_app


def test_tray_icon_generation() -> None:
    icon_img = create_pil_tray_icon()
    assert icon_img.size == (64, 64)
    assert icon_img.mode == "RGBA"


def test_api_full_workflow(tmp_path: Path) -> None:
    cm = ConfigManager(data_dir=tmp_path / "data", storage_root=tmp_path / "storage")
    svc = SyncService(config_manager=cm)
    app = create_app(svc)
    client = TestClient(app)

    # 1. Получение обзора
    r_ov = client.get("/api/overview")
    assert r_ov.status_code == 200
    ov = r_ov.json()
    assert ov["device_role"] == "home_master"
    assert len(ov["folders"]) == 5
    assert "<configuration" in ov["syncthing_xml_preview"]

    # 2. Переключение роли ПК на "Рабочий ПК (Слив файлов)"
    r_role = client.post("/api/role", json={"role": "work_client"})
    assert r_role.status_code == 200
    assert r_role.json()["device_role"] == "work_client"
    work_dump = next(f for f in r_role.json()["folders"] if f["id"] == "work-dump")
    assert work_dump["sync_mode"] == "sendonly"

    # 3. Создание зашифрованного файла в папке Сейфа
    r_file = client.post(
        "/api/folders/secure-vault/files",
        json={
            "file_name": "Ключи_доступа.txt",
            "content_text": "Секретные данные 12345",
            "side": "local",
            "encrypt_passphrase": "VaultPassword2026",
        },
    )
    assert r_file.status_code == 200
    assert r_file.json()["created"]["encrypted"] is True
    assert r_file.json()["created"]["file_name"] == "Ключи_доступа.txt.moultenc"

    # 4. Генерация и импорт зашифрованного токена сопряжения между ПК
    r_inv = client.post(
        "/api/security/pairing-invite",
        json={"pin_code": "MySecretPin", "target_role": "work_client"},
    )
    assert r_inv.status_code == 200
    token = r_inv.json()["token"]
    assert token.startswith("MOULT-PAIR-V1:")

    r_imp = client.post(
        "/api/security/import-invite",
        json={"token": token, "pin_code": "MySecretPin"},
    )
    assert r_imp.status_code == 200
    assert r_imp.json()["imported"]["imported"] is True

    # 5. Добавление пользовательской папки (например, Дистрибутивы) с детерминированным ID
    dist_path = str(tmp_path / "Дистрибутивы")
    r_add = client.post(
        "/api/folders",
        json={
            "label": "Дистрибутивы",
            "path": dist_path,
            "sync_mode": "sendreceive",
        },
    )
    assert r_add.status_code == 200
    assert r_add.json()["folder"]["id"] == "folder-distributivy"
    assert r_add.json()["folder"]["path"] == dist_path
