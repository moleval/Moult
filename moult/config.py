from __future__ import annotations

import json
import os
import platform
import secrets
from pathlib import Path
from typing import Dict, List, Optional

from moult.models import (
    AppConfig,
    DeviceRole,
    PeerDevice,
    SecurityConfig,
    SecurityProfile,
    SyncFolder,
    SyncMode,
    VersioningMode,
)
from moult.security.certificates import ensure_device_identity


class ConfigManager:
    """
    Управляет конфигурацией Moult Диск, ключами безопасности TLS 1.3 и пресетами папок
    для Главного (Домашнего) ПК, Рабочего ПК и Android-смартфона.
    """

    def __init__(self, data_dir: Optional[Path] = None, storage_root: Optional[Path] = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        self.data_dir = (data_dir or (base_dir / ".moult_data")).resolve()
        self.storage_root = (storage_root or (base_dir / "sync_storage")).resolve()
        self.certs_dir = self.data_dir / "certs"
        self.versions_dir = self.data_dir / "versions"
        self.config_file = self.data_dir / "moult_config.json"
        self.events_file = self.data_dir / "sync_events.json"
        self.versions_index_file = self.data_dir / "versions_index.json"

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.certs_dir.mkdir(parents=True, exist_ok=True)
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.storage_root.mkdir(parents=True, exist_ok=True)

        self.device_id, self.cert_path, self.key_path = ensure_device_identity(self.certs_dir)
        self.config: AppConfig = self._load_or_init()

    def _default_folders_for_role(self, role: DeviceRole) -> List[SyncFolder]:
        local_node = self.storage_root / "Локальный_Диск_ПК"
        remote_sim_node = self.storage_root / "Узел_Репликации"
        local_node.mkdir(parents=True, exist_ok=True)
        remote_sim_node.mkdir(parents=True, exist_ok=True)

        work_dump_mode = SyncMode.RECEIVE_ONLY if role == DeviceRole.HOME_MASTER else SyncMode.SEND_ONLY

        folders = [
            SyncFolder(
                id="work-dump",
                label="Слив с Рабочего ПК",
                path=str(local_node / "01_Слив_с_Рабочего_ПК"),
                paired_local_path=str(remote_sim_node / "01_Слив_с_Рабочего_ПК"),
                sync_mode=work_dump_mode,
                category="work_dump",
                encryption_enabled=False,
                versioning_mode=VersioningMode.TRASHCAN,
                versioning_cleanout_days=30,
                rescan_interval_sec=30,
                fs_watcher_enabled=True,
                last_status=(
                    "Приём файлов с Рабочего ПК (Мастер-хранилище)"
                    if role == DeviceRole.HOME_MASTER
                    else "Только отправка (Слив файлов на Главный ПК)"
                ),
            ),
            SyncFolder(
                id="common-docs",
                label="Общие документы (Дом ↔ Работа)",
                path=str(local_node / "02_Общие_Документы"),
                paired_local_path=str(remote_sim_node / "02_Общие_Документы"),
                sync_mode=SyncMode.SEND_RECEIVE,
                category="pc_sync",
                encryption_enabled=False,
                versioning_mode=VersioningMode.SIMPLE,
                versioning_keep_count=15,
                rescan_interval_sec=30,
                fs_watcher_enabled=True,
                last_status="Двусторонняя синхронизация активна",
            ),
            SyncFolder(
                id="android-camera",
                label="Камера Android (DCIM/Camera)",
                path=str(local_node / "03_Камера_Android"),
                paired_local_path=str(remote_sim_node / "03_Камера_Android"),
                sync_mode=SyncMode.RECEIVE_ONLY,
                category="android_camera",
                encryption_enabled=False,
                versioning_mode=VersioningMode.TRASHCAN,
                versioning_cleanout_days=90,
                rescan_interval_sec=60,
                fs_watcher_enabled=True,
                last_status="Ожидание новых фото/видео со смартфона",
            ),
            SyncFolder(
                id="android-files",
                label="Обмен с Android (Двусторонняя)",
                path=str(local_node / "04_Обмен_с_Телефоном"),
                paired_local_path=str(remote_sim_node / "04_Обмен_с_Телефоном"),
                sync_mode=SyncMode.SEND_RECEIVE,
                category="android_sync",
                encryption_enabled=False,
                versioning_mode=VersioningMode.SIMPLE,
                versioning_keep_count=10,
                rescan_interval_sec=45,
                fs_watcher_enabled=True,
                last_status="Двусторонний обмен ПК ↔ Смартфон",
            ),
            SyncFolder(
                id="secure-vault",
                label="Защищённый Сейф и БД (AES-256)",
                path=str(local_node / "05_Сейф_и_Базы_Данных"),
                paired_local_path=str(remote_sim_node / "05_Сейф_и_Базы_Данных"),
                sync_mode=SyncMode.SEND_RECEIVE,
                category="database_safe",
                encryption_enabled=True,
                versioning_mode=VersioningMode.SIMPLE,
                versioning_keep_count=30,
                rescan_interval_sec=60,
                fs_watcher_enabled=True,
                process_guard_enabled=True,
                process_guard_executables=["mmex.exe", "HomeBuh.exe", "MonefySync.exe"],
                last_status="Сквозная защита AES-256-GCM + контроль целостности",
            ),
        ]

        for folder in folders:
            Path(folder.path).mkdir(parents=True, exist_ok=True)
            if folder.paired_local_path:
                Path(folder.paired_local_path).mkdir(parents=True, exist_ok=True)
            self._write_stignore_file(folder)

        self._seed_initial_files(folders)
        return folders

    def _write_stignore_file(self, folder: SyncFolder) -> None:
        folder_path = Path(folder.path)
        folder_path.mkdir(parents=True, exist_ok=True)
        stignore_path = folder_path / ".stignore"
        lines = [
            "// Автоматически сгенерировано Moult Диск",
            "// Временные и системные файлы, исключаемые из синхронизации:",
            ".moult_versions",
            ".stversions",
        ] + folder.ignore_patterns
        stignore_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _seed_initial_files(self, folders: List[SyncFolder]) -> None:
        by_id: Dict[str, SyncFolder] = {f.id: f for f in folders}

        if "common-docs" in by_id:
            docs_dir = Path(by_id["common-docs"].path)
            readme_doc = docs_dir / "Памятка_Синхронизации_Moult.txt"
            if not readme_doc.exists():
                readme_doc.write_text(
                    "Moult Диск — Защищённая синхронизация Домашнего ПК, Рабочего ПК и Android.\n"
                    "Режим папки: Двусторонняя синхронизация (Send & Receive).\n"
                    "Все изменения проверяются по SHA-256 и передаются по защищённому каналу TLS 1.3.\n",
                    encoding="utf-8",
                )

        if "work-dump" in by_id:
            work_dir = Path(by_id["work-dump"].path)
            work_paired = Path(by_id["work-dump"].paired_local_path or work_dir)
            sample_report = work_paired / "Отчёт_с_Рабочего_ПК_2026.txt"
            if not sample_report.exists():
                sample_report.write_text(
                    "Отчёт подготовлен на Рабочем ПК для автоматического слива на Главный домашний ПК.\n"
                    "Статус: Готов к передаче по защищённому каналу.\n",
                    encoding="utf-8",
                )

        if "android-camera" in by_id:
            cam_paired = Path(by_id["android-camera"].paired_local_path or by_id["android-camera"].path)
            sample_photo_meta = cam_paired / "IMG_20261021_Camera_Sync_Info.txt"
            if not sample_photo_meta.exists():
                sample_photo_meta.write_text(
                    "Папка привязана к DCIM/Camera на Android-смартфоне (режим: Только отправка с телефона).\n"
                    "Удаление фото на телефоне НЕ удаляет сохранённые снимки на Главном ПК.\n",
                    encoding="utf-8",
                )

    def _default_peer_devices(self) -> List[PeerDevice]:
        # Создаём детерминированные валидные сертификаты для демонстрационных профилей Рабочего ПК и Android,
        # чтобы пользователь видел реальные валидные Syncthing Device ID и мог заменить их своими в 1 клик.
        work_id, _, _ = ensure_device_identity(self.certs_dir / "peer_work_template", "moult-work-pc")
        android_id, _, _ = ensure_device_identity(self.certs_dir / "peer_android_template", "moult-android")
        return [
            PeerDevice(
                device_id=work_id,
                name="Рабочий ПК (Офис — Слив файлов)",
                role=DeviceRole.WORK_CLIENT,
                addresses=["dynamic"],
                compression="metadata",
                introducer=False,
                connected=True,
                last_seen="Активен (TLS 1.3 mTLS)",
                tls_cipher="TLS_AES_256_GCM_SHA384 (TLS 1.3)",
                trusted_fingerprint=work_id[:15],
            ),
            PeerDevice(
                device_id=android_id,
                name="Смартфон Android (Камера + Обмен)",
                role=DeviceRole.ANDROID_MOBILE,
                addresses=["dynamic"],
                compression="metadata",
                introducer=False,
                connected=False,
                last_seen="Ожидает сопряжения по QR-коду",
                tls_cipher="TLS_CHACHA20_POLY1305_SHA256 (TLS 1.3)",
                trusted_fingerprint=android_id[:15],
            ),
        ]

    def _load_or_init(self) -> AppConfig:
        if self.config_file.exists():
            try:
                raw = json.loads(self.config_file.read_text(encoding="utf-8"))
                cfg = AppConfig(**raw)
                cfg.device_id = self.device_id
                for folder in cfg.folders:
                    Path(folder.path).mkdir(parents=True, exist_ok=True)
                    if folder.paired_local_path:
                        Path(folder.paired_local_path).mkdir(parents=True, exist_ok=True)
                return cfg
            except Exception:
                pass

        hostname = platform.node() or "Windows-PC"
        role = DeviceRole.HOME_MASTER
        folders = self._default_folders_for_role(role)
        peers = self._default_peer_devices()
        peer_ids = [p.device_id for p in peers]
        for folder in folders:
            folder.shared_device_ids = peer_ids

        cfg = AppConfig(
            device_id=self.device_id,
            device_name=f"Главный ПК ({hostname})",
            device_role=role,
            root_disk_path=str(self.storage_root / "Локальный_Диск_ПК"),
            syncthing_api_url="http://127.0.0.1:8384",
            syncthing_api_key=secrets.token_urlsafe(24),
            syncthing_listen_port=22000,
            security=SecurityConfig(
                profile=SecurityProfile.PROTECTED_INTERNET,
                tls_min_version="TLSv1.3",
                verify_sha256_integrity=True,
                api_bind_localhost_only=True,
            ),
            folders=folders,
            devices=peers,
        )
        self.save(cfg)
        return cfg

    def save(self, config: Optional[AppConfig] = None) -> AppConfig:
        if config is not None:
            self.config = config
        for folder in self.config.folders:
            self._write_stignore_file(folder)
        self.config_file.write_text(
            json.dumps(self.config.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.config

    def apply_role_preset(self, role: DeviceRole, custom_name: Optional[str] = None) -> AppConfig:
        """
        Переключает роль текущего ПК между 'Главный ПК (Домашний)' и 'Рабочий ПК (Слив файлов)',
        автоматически перенастраивая направление синхронизации папок (как в Dropsync).
        """
        self.config.device_role = role
        hostname = platform.node() or "Windows-PC"
        if custom_name:
            self.config.device_name = custom_name
        elif role == DeviceRole.HOME_MASTER:
            self.config.device_name = f"Главный ПК — Дом ({hostname})"
        elif role == DeviceRole.WORK_CLIENT:
            self.config.device_name = f"Рабочий ПК — Слив ({hostname})"

        for folder in self.config.folders:
            if folder.id == "work-dump" or folder.category == "work_dump":
                if role == DeviceRole.HOME_MASTER:
                    folder.sync_mode = SyncMode.RECEIVE_ONLY
                    folder.last_status = "Режим Главного ПК: приём сливаемых файлов с работы"
                else:
                    folder.sync_mode = SyncMode.SEND_ONLY
                    folder.last_status = "Режим Рабочего ПК: только отправка (слив файлов домой)"
            elif folder.id == "android-camera" or folder.category == "android_camera":
                folder.sync_mode = SyncMode.RECEIVE_ONLY
                folder.last_status = "Приём фото/видео из папки DCIM/Camera"

        return self.save()
