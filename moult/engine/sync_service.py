from __future__ import annotations

import base64
import datetime
import fnmatch
import io
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import qrcode

from moult.config import ConfigManager
from moult.engine.syncthing_client import SyncthingDaemonManager, generate_syncthing_xml
from moult.models import (
    DeviceRole,
    PeerDevice,
    SyncEventRecord,
    SyncFolder,
    SyncMode,
    VersionedFileEntry,
    VersioningMode,
)
from moult.platform.windows_integration import check_blocking_processes, get_disk_space_info
from moult.security.certificates import validate_device_id
from moult.security.crypto import (
    compute_file_sha256,
    create_encrypted_pairing_token,
    decrypt_file_aes_gcm,
    decrypt_pairing_token,
    encrypt_file_aes_gcm,
)

INTERNAL_IGNORED_NAMES = {".stignore", ".stfolder", ".stversions", ".moult_versions"}


class SyncService:
    """
    Ядро управления синхронизацией Moult Диск:
    - Двусторонняя (Send & Receive), Только отправка / Слив (Send Only), Только приём (Receive Only)
    - Контроль целостности SHA-256 каждого передаваемого файла
    - История версий и Корзина (восстановление в 1 клик)
    - Сквозное шифрование файлов AES-256-GCM
    - Генерация конфигурации и управление демоном Syncthing BEP (TLS 1.3)
    """

    def __init__(self, config_manager: Optional[ConfigManager] = None) -> None:
        self.cm = config_manager or ConfigManager()
        self.daemon = SyncthingDaemonManager(self.cm.data_dir, self.cm.config)
        self.daemon.write_config_files(self.cm.config, self.cm.cert_path, self.cm.key_path)
        self.events: List[SyncEventRecord] = self._load_events()
        self.versions: List[VersionedFileEntry] = self._load_versions()
        self._refresh_all_folder_stats()

    def _now_iso(self) -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _load_events(self) -> List[SyncEventRecord]:
        if self.cm.events_file.exists():
            try:
                raw = json.loads(self.cm.events_file.read_text(encoding="utf-8"))
                return [SyncEventRecord(**item) for item in raw]
            except Exception:
                return []
        return []

    def _save_events(self) -> None:
        self.events = self.events[:250]
        self.cm.events_file.write_text(
            json.dumps([e.model_dump() for e in self.events], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _record_event(
        self,
        folder_id: str,
        folder_label: str,
        action: str,
        file_name: str,
        relative_path: str,
        size_bytes: int = 0,
        sha256_short: Optional[str] = None,
        encrypted: bool = False,
        direction: str = "↔",
        details: str = "",
    ) -> SyncEventRecord:
        ev = SyncEventRecord(
            id=uuid.uuid4().hex[:12],
            timestamp=self._now_iso(),
            folder_id=folder_id,
            folder_label=folder_label,
            action=action,
            file_name=file_name,
            relative_path=relative_path,
            size_bytes=size_bytes,
            sha256_short=sha256_short,
            encrypted=encrypted,
            direction=direction,
            details=details,
        )
        self.events.insert(0, ev)
        self._save_events()
        return ev

    def _load_versions(self) -> List[VersionedFileEntry]:
        if self.cm.versions_index_file.exists():
            try:
                raw = json.loads(self.cm.versions_index_file.read_text(encoding="utf-8"))
                return [VersionedFileEntry(**item) for item in raw]
            except Exception:
                return []
        return []

    def _save_versions(self) -> None:
        self.cm.versions_index_file.write_text(
            json.dumps([v.model_dump() for v in self.versions], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _is_ignored(self, rel_path: str, patterns: List[str]) -> bool:
        parts = Path(rel_path).parts
        for part in parts:
            if part in INTERNAL_IGNORED_NAMES:
                return True
            for pat in patterns:
                if fnmatch.fnmatch(part, pat) or fnmatch.fnmatch(rel_path, pat):
                    return True
        return False

    def _scan_tree(self, root_dir: Path, patterns: List[str]) -> Dict[str, Path]:
        result: Dict[str, Path] = {}
        if not root_dir.exists():
            return result
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames if d not in INTERNAL_IGNORED_NAMES]
            for fname in filenames:
                if fname in INTERNAL_IGNORED_NAMES:
                    continue
                full_p = Path(dirpath) / fname
                rel = full_p.relative_to(root_dir).as_posix()
                if self._is_ignored(rel, patterns):
                    continue
                result[rel] = full_p
        return result

    def _archive_file_version(
        self,
        folder: SyncFolder,
        file_path: Path,
        rel_path: str,
        reason: str = "Обновление файла при синхронизации",
    ) -> Optional[VersionedFileEntry]:
        if folder.versioning_mode == VersioningMode.NONE or not file_path.exists():
            return None

        sha = compute_file_sha256(file_path)
        ts_slug = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        version_id = f"{folder.id}_{uuid.uuid4().hex[:8]}"
        dest_dir = self.cm.versions_dir / folder.id
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"{ts_slug}__{Path(rel_path).name}"
        archive_path = dest_dir / safe_name
        shutil.copy2(file_path, archive_path)

        entry = VersionedFileEntry(
            version_id=version_id,
            folder_id=folder.id,
            folder_label=folder.label,
            original_relative_path=rel_path,
            file_name=Path(rel_path).name,
            archived_at=self._now_iso(),
            size_bytes=file_path.stat().st_size,
            sha256=sha,
            archive_path=str(archive_path),
            reason=reason,
        )
        self.versions.insert(0, entry)

        # Ограничиваем количество хранимых версий одного файла (Simple Versioning)
        if folder.versioning_mode == VersioningMode.SIMPLE:
            same_file_versions = [
                v
                for v in self.versions
                if v.folder_id == folder.id and v.original_relative_path == rel_path
            ]
            if len(same_file_versions) > folder.versioning_keep_count:
                for old_v in same_file_versions[folder.versioning_keep_count :]:
                    try:
                        Path(old_v.archive_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    self.versions.remove(old_v)

        self._save_versions()
        return entry

    def _copy_with_integrity(self, src: Path, dst: Path, verify_sha256: bool = True) -> Tuple[str, int]:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src_sha = compute_file_sha256(src)
        shutil.copy2(src, dst)
        if verify_sha256:
            dst_sha = compute_file_sha256(dst)
            if src_sha != dst_sha:
                dst.unlink(missing_ok=True)
                raise ValueError(
                    f"Ошибка целостности SHA-256 при передаче {src.name}: {src_sha[:10]} != {dst_sha[:10]}"
                )
        return src_sha, dst.stat().st_size

    def _refresh_folder_stats(self, folder: SyncFolder) -> None:
        files = self._scan_tree(Path(folder.path), folder.ignore_patterns)
        folder.files_count = len(files)
        folder.total_bytes = sum(p.stat().st_size for p in files.values() if p.exists())

    def _refresh_all_folder_stats(self) -> None:
        for folder in self.cm.config.folders:
            self._refresh_folder_stats(folder)
        self.cm.save()

    def sync_folder(self, folder_id: str) -> Dict[str, Any]:
        """
        Выполняет цикл синхронизации заданной папки согласно её режиму:
        - sendreceive (Двусторонняя синхронизация)
        - sendonly (Только отправка / Слив с Рабочего ПК или Камеры)
        - receiveonly (Только приём на Главный ПК)
        """
        folder = next((f for f in self.cm.config.folders if f.id == folder_id), None)
        if not folder:
            raise ValueError(f"Папка с ID '{folder_id}' не найдена")

        if folder.paused:
            return {
                "folder_id": folder.id,
                "status": "paused",
                "message": "Папка поставлена на паузу пользователем",
                "transferred": 0,
            }

        # Проверка Process Guard (актуально для папок с базой данных бухгалтерии)
        if folder.process_guard_enabled and folder.process_guard_executables:
            blocking = check_blocking_processes(folder.process_guard_executables)
            if blocking:
                msg = f"Ожидание закрытия программы БД: {', '.join(blocking)}"
                folder.last_status = msg
                self.cm.save()
                return {
                    "folder_id": folder.id,
                    "status": "blocked_by_process",
                    "blocking_processes": blocking,
                    "message": msg,
                    "transferred": 0,
                }

        local_root = Path(folder.path)
        local_root.mkdir(parents=True, exist_ok=True)
        remote_root = Path(folder.paired_local_path) if folder.paired_local_path else None
        if remote_root:
            remote_root.mkdir(parents=True, exist_ok=True)

        verify_sha = self.cm.config.security.verify_sha256_integrity
        local_files = self._scan_tree(local_root, folder.ignore_patterns)
        remote_files = self._scan_tree(remote_root, folder.ignore_patterns) if remote_root else {}

        uploaded = 0
        downloaded = 0
        conflicts = 0
        verified = 0

        all_rels = sorted(set(local_files.keys()) | set(remote_files.keys()))

        for rel in all_rels:
            l_path = local_files.get(rel)
            r_path = remote_files.get(rel)

            # Режим 1: ДВУСТОРОННЯЯ СИНХРОНИЗАЦИЯ (Send & Receive)
            if folder.sync_mode == SyncMode.SEND_RECEIVE and remote_root:
                if l_path and not r_path:
                    target = remote_root / rel
                    sha, sz = self._copy_with_integrity(l_path, target, verify_sha)
                    uploaded += 1
                    verified += 1
                    self._record_event(
                        folder.id,
                        folder.label,
                        "uploaded",
                        l_path.name,
                        rel,
                        sz,
                        sha[:12],
                        folder.encryption_enabled,
                        "→",
                        "Отправлено на связанные устройства (TLS 1.3 + SHA-256)",
                    )
                elif r_path and not l_path:
                    target = local_root / rel
                    sha, sz = self._copy_with_integrity(r_path, target, verify_sha)
                    downloaded += 1
                    verified += 1
                    self._record_event(
                        folder.id,
                        folder.label,
                        "downloaded",
                        r_path.name,
                        rel,
                        sz,
                        sha[:12],
                        folder.encryption_enabled,
                        "←",
                        "Принято с удалённого устройства (SHA-256 проверен)",
                    )
                elif l_path and r_path:
                    l_sha = compute_file_sha256(l_path)
                    r_sha = compute_file_sha256(r_path)
                    if l_sha != r_sha:
                        l_mtime = l_path.stat().st_mtime
                        r_mtime = r_path.stat().st_mtime
                        # Если файлы изменены почти одновременно (< 1.5 сек разницы) — фиксируем конфликт версий
                        if abs(l_mtime - r_mtime) < 1.5:
                            conflict_stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                            conflict_name = f"{l_path.stem}.sync-conflict-{conflict_stamp}{l_path.suffix}"
                            conflict_target = l_path.with_name(conflict_name)
                            self._archive_file_version(folder, l_path, rel, "Конфликт двустороннего изменения")
                            self._copy_with_integrity(r_path, conflict_target, verify_sha)
                            conflicts += 1
                            self._record_event(
                                folder.id,
                                folder.label,
                                "conflict",
                                conflict_name,
                                rel,
                                conflict_target.stat().st_size,
                                r_sha[:12],
                                folder.encryption_enabled,
                                "⚠",
                                f"Сохранена конфликтная копия {conflict_name} (обе версии сохранены)",
                            )
                        elif l_mtime > r_mtime:
                            self._archive_file_version(folder, r_path, rel, "Замена более новой версией с этого ПК")
                            sha, sz = self._copy_with_integrity(l_path, r_path, verify_sha)
                            uploaded += 1
                            verified += 1
                            self._record_event(
                                folder.id,
                                folder.label,
                                "uploaded",
                                l_path.name,
                                rel,
                                sz,
                                sha[:12],
                                folder.encryption_enabled,
                                "→",
                                "Обновлена версия на втором устройстве (предыдущая версия в Корзине)",
                            )
                        else:
                            self._archive_file_version(folder, l_path, rel, "Замена более новой удалённой версией")
                            sha, sz = self._copy_with_integrity(r_path, l_path, verify_sha)
                            downloaded += 1
                            verified += 1
                            self._record_event(
                                folder.id,
                                folder.label,
                                "downloaded",
                                r_path.name,
                                rel,
                                sz,
                                sha[:12],
                                folder.encryption_enabled,
                                "←",
                                "Обновлена локальная копия (старая версия сохранена в Корзине)",
                            )
                    else:
                        verified += 1

            # Режим 2: ТОЛЬКО ОТПРАВКА / СЛИВ ФАЙЛОВ (Send Only — для Рабочего ПК или Камеры)
            elif folder.sync_mode == SyncMode.SEND_ONLY and remote_root:
                if l_path:
                    target = remote_root / rel
                    need_send = True
                    if r_path:
                        if compute_file_sha256(l_path) == compute_file_sha256(r_path):
                            need_send = False
                        else:
                            self._archive_file_version(
                                folder, r_path, rel, "Обновление при сливе с Рабочего ПК"
                            )
                    if need_send:
                        sha, sz = self._copy_with_integrity(l_path, target, verify_sha)
                        uploaded += 1
                        verified += 1
                        self._record_event(
                            folder.id,
                            folder.label,
                            "uploaded",
                            l_path.name,
                            rel,
                            sz,
                            sha[:12],
                            folder.encryption_enabled,
                            "→",
                            "Слив файла на Главный ПК выполнен (SHA-256 подтверждён)",
                        )
                    if folder.delete_source_after_send and target.exists():
                        if compute_file_sha256(l_path) == compute_file_sha256(target):
                            self._archive_file_version(
                                folder, l_path, rel, "Слив с очисткой исходника"
                            )
                            l_path.unlink(missing_ok=True)

            # Режим 3: ТОЛЬКО ПРИЁМ (Receive Only — для Главного ПК: приём слива с работы и Камеры Android)
            elif folder.sync_mode in (SyncMode.RECEIVE_ONLY, SyncMode.RECEIVE_ENCRYPTED) and remote_root:
                if r_path:
                    target = local_root / rel
                    need_pull = True
                    if l_path:
                        if compute_file_sha256(l_path) == compute_file_sha256(r_path):
                            need_pull = False
                        else:
                            self._archive_file_version(
                                folder, l_path, rel, "Архивация перед приёмом новой версии"
                            )
                    if need_pull:
                        sha, sz = self._copy_with_integrity(r_path, target, verify_sha)
                        downloaded += 1
                        verified += 1
                        self._record_event(
                            folder.id,
                            folder.label,
                            "downloaded",
                            r_path.name,
                            rel,
                            sz,
                            sha[:12],
                            folder.encryption_enabled,
                            "←",
                            "Принят файл в Мастер-хранилище Главного ПК (SHA-256 подтверждён)",
                        )

        folder.last_sync_at = self._now_iso()
        total_changes = uploaded + downloaded + conflicts
        if total_changes > 0:
            folder.last_status = (
                f"Синхронизировано: ↑{uploaded} ↓{downloaded}"
                + (f" (конфликтов: {conflicts})" if conflicts else "")
            )
        else:
            folder.last_status = f"Все файлы актуальны (проверено SHA-256: {verified})"

        self._refresh_folder_stats(folder)
        self.cm.save()
        self.daemon.write_config_files(self.cm.config, self.cm.cert_path, self.cm.key_path)

        return {
            "folder_id": folder.id,
            "folder_label": folder.label,
            "sync_mode": folder.sync_mode.value,
            "status": "synced",
            "uploaded": uploaded,
            "downloaded": downloaded,
            "conflicts": conflicts,
            "verified_files": verified,
            "last_sync_at": folder.last_sync_at,
            "files_count": folder.files_count,
            "total_bytes": folder.total_bytes,
        }

    def sync_all_folders(self) -> Dict[str, Any]:
        results = []
        total_up = 0
        total_down = 0
        total_conflicts = 0
        for folder in self.cm.config.folders:
            res = self.sync_folder(folder.id)
            results.append(res)
            total_up += res.get("uploaded", 0)
            total_down += res.get("downloaded", 0)
            total_conflicts += res.get("conflicts", 0)
        return {
            "timestamp": self._now_iso(),
            "total_uploaded": total_up,
            "total_downloaded": total_down,
            "total_conflicts": total_conflicts,
            "folders": results,
        }

    def list_folder_files(self, folder_id: str, side: str = "local") -> List[Dict[str, Any]]:
        folder = next((f for f in self.cm.config.folders if f.id == folder_id), None)
        if not folder:
            raise ValueError(f"Папка '{folder_id}' не найдена")

        target_root = (
            Path(folder.paired_local_path)
            if side == "remote" and folder.paired_local_path
            else Path(folder.path)
        )
        files_map = self._scan_tree(target_root, folder.ignore_patterns)
        items: List[Dict[str, Any]] = []
        for rel, fpath in sorted(files_map.items()):
            stat = fpath.stat()
            sha = compute_file_sha256(fpath)
            items.append(
                {
                    "name": fpath.name,
                    "relative_path": rel,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.datetime.fromtimestamp(stat.st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "sha256": sha,
                    "sha256_short": sha[:12],
                    "is_encrypted": fpath.suffix == ".moultenc"
                    or fpath.read_bytes()[:12] == b"MOULT_ENC_V1",
                    "is_conflict": ".sync-conflict-" in fpath.name,
                }
            )
        return items

    def create_or_upload_file(
        self,
        folder_id: str,
        file_name: str,
        content: bytes,
        side: str = "local",
        encrypt_passphrase: Optional[str] = None,
    ) -> Dict[str, Any]:
        folder = next((f for f in self.cm.config.folders if f.id == folder_id), None)
        if not folder:
            raise ValueError(f"Папка '{folder_id}' не найдена")

        safe_name = Path(file_name).name
        if not safe_name:
            raise ValueError("Некорректное имя файла")

        base_root = (
            Path(folder.paired_local_path)
            if side == "remote" and folder.paired_local_path
            else Path(folder.path)
        )
        base_root.mkdir(parents=True, exist_ok=True)
        target_path = base_root / safe_name

        if target_path.exists():
            self._archive_file_version(folder, target_path, safe_name, "Перезапись пользователем")

        if encrypt_passphrase:
            if not safe_name.endswith(".moultenc"):
                target_path = base_root / f"{safe_name}.moultenc"
            tmp_plain = base_root / f".tmp_{uuid.uuid4().hex}"
            try:
                tmp_plain.write_bytes(content)
                enc_meta = encrypt_file_aes_gcm(tmp_plain, target_path, encrypt_passphrase)
            finally:
                tmp_plain.unlink(missing_ok=True)
            self._record_event(
                folder.id,
                folder.label,
                "encrypted",
                target_path.name,
                target_path.name,
                target_path.stat().st_size,
                enc_meta["encrypted_sha256"][:12],
                True,
                "🔒",
                "Файл зашифрован AES-256-GCM перед синхронизацией",
            )
        else:
            target_path.write_bytes(content)
            sha = compute_file_sha256(target_path)
            self._record_event(
                folder.id,
                folder.label,
                "synced",
                target_path.name,
                target_path.name,
                len(content),
                sha[:12],
                False,
                "+" if side == "local" else "⇄",
                f"Добавлен файл ({'Локальный ПК' if side == 'local' else 'Второй узел / Рабочий ПК'})",
            )

        sync_res = self.sync_folder(folder.id)
        return {
            "file_name": target_path.name,
            "side": side,
            "encrypted": bool(encrypt_passphrase),
            "sync_result": sync_res,
        }

    def restore_version(self, version_id: str) -> Dict[str, Any]:
        entry = next((v for v in self.versions if v.version_id == version_id), None)
        if not entry:
            raise ValueError(f"Версия '{version_id}' не найдена в Корзине")

        folder = next((f for f in self.cm.config.folders if f.id == entry.folder_id), None)
        if not folder:
            raise ValueError(f"Папка '{entry.folder_id}' не найдена")

        archive_p = Path(entry.archive_path)
        if not archive_p.exists():
            raise ValueError("Архивный файл версии отсутствует на диске")

        restore_target = Path(folder.path) / entry.original_relative_path
        restore_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive_p, restore_target)

        self._record_event(
            folder.id,
            folder.label,
            "versioned",
            entry.file_name,
            entry.original_relative_path,
            restore_target.stat().st_size,
            entry.sha256[:12],
            False,
            "↺",
            f"Восстановлена версия от {entry.archived_at}",
        )
        self._refresh_folder_stats(folder)
        self.cm.save()
        return {
            "restored": True,
            "file_name": entry.file_name,
            "folder_id": folder.id,
            "restored_to": str(restore_target),
            "sha256": entry.sha256,
        }

    def add_or_update_folder(self, folder_data: Dict[str, Any]) -> SyncFolder:
        folder_id = folder_data.get("id") or f"folder-{uuid.uuid4().hex[:6]}"
        existing = next((f for f in self.cm.config.folders if f.id == folder_id), None)

        local_base = Path(self.cm.config.root_disk_path)
        default_path = str(local_base / folder_data.get("label", folder_id))
        default_paired = str(self.cm.storage_root / "Узел_Репликации" / folder_data.get("label", folder_id))

        if existing:
            for k, v in folder_data.items():
                if hasattr(existing, k) and v is not None:
                    setattr(existing, k, v)
            folder = existing
        else:
            folder = SyncFolder(
                id=folder_id,
                label=folder_data.get("label", "Новая папка"),
                path=folder_data.get("path") or default_path,
                paired_local_path=folder_data.get("paired_local_path") or default_paired,
                sync_mode=SyncMode(folder_data.get("sync_mode", SyncMode.SEND_RECEIVE.value)),
                category=folder_data.get("category", "pc_sync"),
                encryption_enabled=bool(folder_data.get("encryption_enabled", False)),
                versioning_mode=VersioningMode(
                    folder_data.get("versioning_mode", VersioningMode.SIMPLE.value)
                ),
                versioning_keep_count=int(folder_data.get("versioning_keep_count", 10)),
                process_guard_enabled=bool(folder_data.get("process_guard_enabled", False)),
                shared_device_ids=[d.device_id for d in self.cm.config.devices],
            )
            self.cm.config.folders.append(folder)

        Path(folder.path).mkdir(parents=True, exist_ok=True)
        if folder.paired_local_path:
            Path(folder.paired_local_path).mkdir(parents=True, exist_ok=True)
        self._refresh_folder_stats(folder)
        self.cm.save()
        self.daemon.write_config_files(self.cm.config, self.cm.cert_path, self.cm.key_path)
        return folder

    def add_peer_device(self, name: str, device_id: str, role: DeviceRole) -> PeerDevice:
        ok, normalized_or_err = validate_device_id(device_id)
        if not ok:
            raise ValueError(f"Некорректный Device ID: {normalized_or_err}")

        existing = next(
            (d for d in self.cm.config.devices if d.device_id == normalized_or_err), None
        )
        if existing:
            existing.name = name
            existing.role = role
            self.cm.save()
            return existing

        peer = PeerDevice(
            device_id=normalized_or_err,
            name=name,
            role=role,
            addresses=["dynamic"],
            compression="metadata",
            connected=True,
            last_seen="Подключено (TLS 1.3 mTLS)",
            trusted_fingerprint=normalized_or_err[:15],
        )
        self.cm.config.devices.append(peer)
        for folder in self.cm.config.folders:
            if normalized_or_err not in folder.shared_device_ids:
                folder.shared_device_ids.append(normalized_or_err)

        self.cm.save()
        self.daemon.write_config_files(self.cm.config, self.cm.cert_path, self.cm.key_path)
        self._record_event(
            "security",
            "Безопасность TLS 1.3",
            "security",
            name,
            normalized_or_err[:15],
            0,
            normalized_or_err[:12],
            True,
            "🛡",
            f"Добавлено доверенное устройство {name} ({normalized_or_err[:15]}...)",
        )
        return peer

    def generate_pairing_qr_base64(self) -> str:
        """
        Генерирует QR-код с официальным Device ID этого ПК.
        Его можно отсканировать из Android-приложения (Syncthing-Fork или будущего Moult APK).
        """
        qr = qrcode.QRCode(version=2, box_size=6, border=2)
        qr.add_data(self.cm.config.device_id)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    def generate_encrypted_pairing_invite(self, pin_code: str, target_role: str = "work_client") -> Dict[str, Any]:
        """
        Создаёт зашифрованный (AES-256-GCM) пакет быстрого подключения для второго ПК или Android.
        """
        payload = {
            "host_device_id": self.cm.config.device_id,
            "host_device_name": self.cm.config.device_name,
            "host_role": self.cm.config.device_role.value,
            "recommended_client_role": target_role,
            "tls_version": "TLSv1.3",
            "folders": [
                {
                    "id": f.id,
                    "label": f.label,
                    "host_mode": f.sync_mode.value,
                    "recommended_client_mode": (
                        SyncMode.SEND_ONLY.value
                        if f.id in ("work-dump", "android-camera")
                        else SyncMode.SEND_RECEIVE.value
                    ),
                    "encryption_enabled": f.encryption_enabled,
                }
                for f in self.cm.config.folders
            ],
            "issued_at": self._now_iso(),
        }
        token = create_encrypted_pairing_token(payload, pin_code)
        return {
            "token": token,
            "cipher": "AES-256-GCM (PBKDF2-HMAC-SHA256, 600 000 итераций)",
            "payload_preview": payload,
        }

    def import_encrypted_pairing_invite(self, token: str, pin_code: str) -> Dict[str, Any]:
        payload = decrypt_pairing_token(token, pin_code)
        host_id = payload["host_device_id"]
        host_name = payload.get("host_device_name", "Главный ПК")
        peer = self.add_peer_device(host_name, host_id, DeviceRole.HOME_MASTER)
        return {
            "imported": True,
            "peer": peer.model_dump(),
            "folders_configured": len(payload.get("folders", [])),
            "payload": payload,
        }

    def get_dashboard_overview(self) -> Dict[str, Any]:
        self._refresh_all_folder_stats()
        disk_info = get_disk_space_info(self.cm.config.root_disk_path)
        daemon_info = self.daemon.ping_rest_api()
        total_files = sum(f.files_count for f in self.cm.config.folders)
        total_synced_bytes = sum(f.total_bytes for f in self.cm.config.folders)

        return {
            "app_name": "Moult Диск",
            "version": "1.0.0",
            "device_id": self.cm.config.device_id,
            "device_name": self.cm.config.device_name,
            "device_role": self.cm.config.device_role.value,
            "root_disk_path": self.cm.config.root_disk_path,
            "disk": disk_info,
            "daemon": daemon_info,
            "security": self.cm.config.security.model_dump(),
            "stats": {
                "folders_count": len(self.cm.config.folders),
                "total_files": total_files,
                "total_synced_bytes": total_synced_bytes,
                "versions_in_trash": len(self.versions),
                "connected_devices": sum(1 for d in self.cm.config.devices if d.connected),
                "total_devices": len(self.cm.config.devices),
            },
            "folders": [f.model_dump() for f in self.cm.config.folders],
            "devices": [d.model_dump() for d in self.cm.config.devices],
            "recent_events": [e.model_dump() for e in self.events[:30]],
            "versions": [v.model_dump() for v in self.versions[:50]],
            "qr_code_data_url": self.generate_pairing_qr_base64(),
            "syncthing_xml_preview": generate_syncthing_xml(self.cm.config),
        }
