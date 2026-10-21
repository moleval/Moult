from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class DeviceRole(str, Enum):
    HOME_MASTER = "home_master"        # Главный ПК (Домашний — центр хранения)
    WORK_CLIENT = "work_client"        # Рабочий ПК (Слив файлов + рабочие папки)
    ANDROID_MOBILE = "android_mobile"  # Смартфон Android (Камера + обмен)


class SyncMode(str, Enum):
    SEND_RECEIVE = "sendreceive"          # Двусторонняя синхронизация (2-Way)
    SEND_ONLY = "sendonly"                # Только отправка / Слив файлов (Upload Only)
    RECEIVE_ONLY = "receiveonly"          # Только приём / Зеркало (Download Only)
    RECEIVE_ENCRYPTED = "receiveencrypted"  # Приём в зашифрованном виде (Untrusted)


class VersioningMode(str, Enum):
    TRASHCAN = "trashcan"      # Корзина (как в Яндекс.Диске, очистка через N дней)
    SIMPLE = "simple"          # Хранить N последних версий каждого файла
    STAGGERED = "staggered"    # Каскадное хранение версий по времени
    NONE = "none"              # Без версионирования


class SecurityProfile(str, Enum):
    MAXIMUM_DIRECT = "maximum_direct"          # Только прямое соединение TLS 1.3 (LAN / VPN, без публичных релеев)
    PROTECTED_INTERNET = "protected_internet"  # Защищённый интернет (mTLS 1.3 + пиннинг сертификатов + E2EE)


class PeerDevice(BaseModel):
    device_id: str = Field(..., description="Криптографический ID устройства (SHA-256 от сертификата в формате Base32+Luhn)")
    name: str = Field(..., description="Имя устройства")
    role: DeviceRole = Field(default=DeviceRole.WORK_CLIENT)
    addresses: List[str] = Field(default_factory=lambda: ["dynamic"], description="Адреса подключения (tcp://..., dynamic)")
    compression: str = Field(default="metadata", description="Сжатие трафика: metadata, always, never")
    introducer: bool = Field(default=False, description="Главный узел-координатор (Домашний ПК)")
    paused: bool = Field(default=False)
    connected: bool = Field(default=False)
    last_seen: Optional[str] = None
    tls_cipher: str = Field(default="TLS_AES_256_GCM_SHA384 (TLS 1.3)")
    trusted_fingerprint: Optional[str] = None


class SyncFolder(BaseModel):
    id: str = Field(..., description="Уникальный идентификатор папки")
    label: str = Field(..., description="Название папки в интерфейсе")
    path: str = Field(..., description="Локальный путь на этом устройстве")
    paired_local_path: Optional[str] = Field(
        default=None,
        description="Локальный путь зеркала/второго узла (для прямой LAN/SMB репликации или локального стенда)",
    )
    sync_mode: SyncMode = Field(default=SyncMode.SEND_RECEIVE, description="Режим синхронизации (как в Dropsync)")
    category: str = Field(default="pc_sync", description="pc_sync | work_dump | android_camera | android_sync | database_safe")
    encryption_enabled: bool = Field(default=False, description="Сквозное шифрование файлов AES-256-GCM / XChaCha20")
    encryption_password_set: bool = Field(default=False)
    versioning_mode: VersioningMode = Field(default=VersioningMode.SIMPLE)
    versioning_keep_count: int = Field(default=10, ge=1, le=500)
    versioning_cleanout_days: int = Field(default=30, ge=1, le=365)
    rescan_interval_sec: int = Field(default=60, ge=5, le=86400)
    fs_watcher_enabled: bool = Field(default=True, description="Отслеживание изменений в реальном времени")
    delete_source_after_send: bool = Field(
        default=False,
        description="Режим 'Слив с очисткой' (переместить на Главный ПК и освободить место после проверки SHA-256)",
    )
    ignore_patterns: List[str] = Field(
        default_factory=lambda: [
            "~$*",
            ".~lock.*",
            "*.tmp",
            "Thumbs.db",
            "desktop.ini",
            ".DS_Store",
        ]
    )
    shared_device_ids: List[str] = Field(default_factory=list)
    process_guard_enabled: bool = Field(
        default=False,
        description="Защита БД (ставить на паузу, пока открыта программа бухгалтерии)",
    )
    process_guard_executables: List[str] = Field(
        default_factory=lambda: ["mmex.exe", "HomeBuh.exe", "1cv8.exe"]
    )
    paused: bool = Field(default=False)
    last_sync_at: Optional[str] = None
    last_status: str = Field(default="Готово к синхронизации")
    files_count: int = Field(default=0)
    total_bytes: int = Field(default=0)


class SecurityConfig(BaseModel):
    profile: SecurityProfile = Field(default=SecurityProfile.PROTECTED_INTERNET)
    tls_min_version: str = Field(default="TLSv1.3")
    cipher_suite: str = Field(default="TLS_AES_256_GCM_SHA384 / XChaCha20-Poly1305")
    relays_enabled: bool = Field(default=True, description="Разрешить зашифрованные релеи при отсутствии прямого IP")
    global_discovery_enabled: bool = Field(default=True, description="Глобальное обнаружение узлов по Device ID")
    local_discovery_enabled: bool = Field(default=True, description="Поиск устройств в локальной сети Wi-Fi / LAN")
    nat_traversal_enabled: bool = Field(default=True, description="UPnP / NAT-PMP проброс портов")
    verify_sha256_integrity: bool = Field(default=True, description="Проверка контрольных сумм SHA-256 после передачи")
    api_bind_localhost_only: bool = Field(default=True, description="Слушать управляющий API только на 127.0.0.1")
    autostart_windows: bool = Field(default=True, description="Запускать автоматически при входе в Windows")
    limit_upload_kbps: int = Field(default=0, ge=0, description="Ограничение скорости отдачи (0 = без лимита)")
    limit_download_kbps: int = Field(default=0, ge=0, description="Ограничение скорости приёма (0 = без лимита)")


class SyncEventRecord(BaseModel):
    id: str
    timestamp: str
    folder_id: str
    folder_label: str
    action: str  # synced | uploaded | downloaded | encrypted | versioned | conflict | deleted | security
    file_name: str
    relative_path: str
    size_bytes: int = 0
    sha256_short: Optional[str] = None
    encrypted: bool = False
    direction: str = "↔"
    details: str = ""


class VersionedFileEntry(BaseModel):
    version_id: str
    folder_id: str
    folder_label: str
    original_relative_path: str
    file_name: str
    archived_at: str
    size_bytes: int
    sha256: str
    archive_path: str
    reason: str = "Изменение / перезапись"


class AppConfig(BaseModel):
    device_id: str
    device_name: str
    device_role: DeviceRole = DeviceRole.HOME_MASTER
    root_disk_path: str
    syncthing_api_url: str = "http://127.0.0.1:8384"
    syncthing_api_key: str
    syncthing_listen_port: int = 22000
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    folders: List[SyncFolder] = Field(default_factory=list)
    devices: List[PeerDevice] = Field(default_factory=list)
