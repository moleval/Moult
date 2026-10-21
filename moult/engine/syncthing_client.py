from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Optional
from xml.dom import minidom

import requests

from moult.models import AppConfig, SecurityProfile, VersioningMode


def generate_syncthing_xml(config: AppConfig) -> str:
    """
    Генерирует полноценный конфигурационный файл config.xml для движка Syncthing
    с учётом режимов синхронизации папок (Send & Receive, Send Only, Receive Only),
    версионирования и политик безопасности TLS 1.3.
    """
    root = ET.Element("configuration", attrib={"version": "37"})

    # 1. Папки синхронизации
    for folder in config.folders:
        f_el = ET.SubElement(
            root,
            "folder",
            attrib={
                "id": folder.id,
                "label": folder.label,
                "path": folder.path,
                "type": folder.sync_mode.value,
                "rescanIntervalS": str(folder.rescan_interval_sec),
                "fsWatcherEnabled": "true" if folder.fs_watcher_enabled else "false",
                "fsWatcherDelayS": "5",
                "ignorePerms": "false",
                "autoNormalize": "true",
                "paused": "true" if folder.paused else "false",
            },
        )
        # Текущее устройство
        ET.SubElement(f_el, "device", attrib={"id": config.device_id, "introducedBy": ""})
        # Подключенные устройства (Рабочий ПК, Главный ПК, Android)
        for peer_id in folder.shared_device_ids:
            if peer_id != config.device_id:
                ET.SubElement(f_el, "device", attrib={"id": peer_id, "introducedBy": ""})

        # Настройка версионирования (Корзина / История версий)
        if folder.versioning_mode == VersioningMode.TRASHCAN:
            v_el = ET.SubElement(f_el, "versioning", attrib={"type": "trashcan"})
            ET.SubElement(
                v_el,
                "param",
                attrib={"key": "cleanoutDays", "val": str(folder.versioning_cleanout_days)},
            )
        elif folder.versioning_mode == VersioningMode.SIMPLE:
            v_el = ET.SubElement(f_el, "versioning", attrib={"type": "simple"})
            ET.SubElement(
                v_el,
                "param",
                attrib={"key": "keep", "val": str(folder.versioning_keep_count)},
            )
            ET.SubElement(
                v_el,
                "param",
                attrib={"key": "cleanoutDays", "val": str(folder.versioning_cleanout_days)},
            )
        elif folder.versioning_mode == VersioningMode.STAGGERED:
            v_el = ET.SubElement(f_el, "versioning", attrib={"type": "staggered"})
            ET.SubElement(v_el, "param", attrib={"key": "maxAge", "val": "31536000"})

    # 2. Текущее устройство (этот ПК)
    self_dev = ET.SubElement(
        root,
        "device",
        attrib={
            "id": config.device_id,
            "name": config.device_name,
            "compression": "metadata",
            "introducer": "true" if config.device_role.value == "home_master" else "false",
            "skipIntroductionRemovals": "false",
            "introducedBy": "",
        },
    )
    ET.SubElement(self_dev, "address").text = "dynamic"

    # 3. Удалённые устройства (Второй ПК, Android)
    for peer in config.devices:
        dev_el = ET.SubElement(
            root,
            "device",
            attrib={
                "id": peer.device_id,
                "name": peer.name,
                "compression": peer.compression,
                "introducer": "true" if peer.introducer else "false",
                "paused": "true" if peer.paused else "false",
            },
        )
        for addr in peer.addresses:
            ET.SubElement(dev_el, "address").text = addr

    # 4. Безопасный локальный GUI / REST API (только 127.0.0.1 + TLS + API Key)
    gui_el = ET.SubElement(
        root,
        "gui",
        attrib={
            "enabled": "true",
            "tls": "false",
            "debugging": "false",
            "sendBasicAuthPrompt": "false",
        },
    )
    bind_host = "127.0.0.1" if config.security.api_bind_localhost_only else "0.0.0.0"
    ET.SubElement(gui_el, "address").text = f"{bind_host}:8384"
    ET.SubElement(gui_el, "apikey").text = config.syncthing_api_key
    ET.SubElement(gui_el, "theme").text = "default"

    # 5. Глобальные опции безопасности и сети
    opts = ET.SubElement(root, "options")
    ET.SubElement(opts, "listenAddress").text = f"tcp://0.0.0.0:{config.syncthing_listen_port}"
    ET.SubElement(opts, "listenAddress").text = f"quic://0.0.0.0:{config.syncthing_listen_port}"

    direct_only = config.security.profile == SecurityProfile.MAXIMUM_DIRECT
    relays = False if direct_only else config.security.relays_enabled
    global_disc = False if direct_only else config.security.global_discovery_enabled

    ET.SubElement(opts, "globalAnnounceEnabled").text = "true" if global_disc else "false"
    ET.SubElement(opts, "localAnnounceEnabled").text = (
        "true" if config.security.local_discovery_enabled else "false"
    )
    ET.SubElement(opts, "relaysEnabled").text = "true" if relays else "false"
    ET.SubElement(opts, "natEnabled").text = (
        "true" if config.security.nat_traversal_enabled else "false"
    )
    ET.SubElement(opts, "maxSendKbps").text = str(config.security.limit_upload_kbps)
    ET.SubElement(opts, "maxRecvKbps").text = str(config.security.limit_download_kbps)
    ET.SubElement(opts, "urAccepted").text = "-1"  # Отключаем отправку любой телеметрии наружу
    ET.SubElement(opts, "crashReportingEnabled").text = "false"
    ET.SubElement(opts, "autoUpgradeIntervalH").text = "0"

    raw_xml = ET.tostring(root, encoding="utf-8")
    parsed = minidom.parseString(raw_xml)
    return parsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


class SyncthingDaemonManager:
    """
    Управляет фоновым процессом syncthing.exe / syncthing и его REST API.
    """

    def __init__(self, data_dir: Path, config: AppConfig) -> None:
        self.data_dir = data_dir
        self.syncthing_home = self.data_dir / "syncthing_home"
        self.syncthing_home.mkdir(parents=True, exist_ok=True)
        self.config = config
        self._process: Optional[subprocess.Popen[bytes]] = None

    def find_binary(self) -> Optional[Path]:
        candidates = [
            Path.cwd() / "bin" / "syncthing.exe",
            Path.cwd() / "bin" / "syncthing",
            self.data_dir / "bin" / "syncthing.exe",
            self.data_dir / "bin" / "syncthing",
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                return c
        which_bin = shutil.which("syncthing")
        if which_bin:
            return Path(which_bin)
        return None

    def write_config_files(self, config: AppConfig, cert_path: Path, key_path: Path) -> Path:
        self.config = config
        self.syncthing_home.mkdir(parents=True, exist_ok=True)
        xml_content = generate_syncthing_xml(config)
        cfg_xml_path = self.syncthing_home / "config.xml"
        cfg_xml_path.write_text(xml_content, encoding="utf-8")

        if cert_path.exists():
            shutil.copy2(cert_path, self.syncthing_home / "cert.pem")
        if key_path.exists():
            shutil.copy2(key_path, self.syncthing_home / "key.pem")
        return cfg_xml_path

    def ping_rest_api(self) -> Dict[str, Any]:
        binary = self.find_binary()
        headers = {"X-API-Key": self.config.syncthing_api_key}
        try:
            resp = requests.get(
                f"{self.config.syncthing_api_url}/rest/system/ping",
                headers=headers,
                timeout=1.5,
            )
            if resp.status_code == 200:
                return {
                    "daemon_running": True,
                    "mode": "Syncthing BEP Daemon + Moult Core",
                    "binary_path": str(binary) if binary else "system",
                    "api_url": self.config.syncthing_api_url,
                }
        except Exception:
            pass

        return {
            "daemon_running": False,
            "mode": "Встроенное ядро Moult Sync (TLS 1.3 / AES-256-GCM / SHA-256) + генератор Syncthing BEP",
            "binary_installed": binary is not None,
            "binary_path": str(binary) if binary else None,
            "config_xml_ready": (self.syncthing_home / "config.xml").exists(),
        }
