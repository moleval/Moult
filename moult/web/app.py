from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from moult.engine.sync_service import SyncService
from moult.models import DeviceRole, SecurityProfile
from moult.platform.windows_integration import open_in_file_manager, set_windows_autostart
from moult.security.certificates import validate_device_id


class RoleUpdatePayload(BaseModel):
    role: DeviceRole
    custom_name: Optional[str] = None


class FolderUpdatePayload(BaseModel):
    id: Optional[str] = None
    label: Optional[str] = None
    path: Optional[str] = None
    paired_local_path: Optional[str] = None
    sync_mode: Optional[str] = None
    category: Optional[str] = None
    encryption_enabled: Optional[bool] = None
    versioning_mode: Optional[str] = None
    versioning_keep_count: Optional[int] = None
    delete_source_after_send: Optional[bool] = None
    process_guard_enabled: Optional[bool] = None
    paused: Optional[bool] = None


class FileCreatePayload(BaseModel):
    file_name: str
    content_text: str
    side: str = "local"  # "local" (Этот ПК) | "remote" (Второй ПК / Смартфон Android)
    encrypt_passphrase: Optional[str] = None


class SecurityUpdatePayload(BaseModel):
    profile: Optional[SecurityProfile] = None
    relays_enabled: Optional[bool] = None
    global_discovery_enabled: Optional[bool] = None
    local_discovery_enabled: Optional[bool] = None
    verify_sha256_integrity: Optional[bool] = None
    api_bind_localhost_only: Optional[bool] = None
    autostart_windows: Optional[bool] = None


class DeviceIdCheckPayload(BaseModel):
    device_id: str


class AddDevicePayload(BaseModel):
    name: str
    device_id: str
    role: DeviceRole = DeviceRole.WORK_CLIENT


class PairingInviteCreatePayload(BaseModel):
    pin_code: str
    target_role: str = "work_client"


class PairingInviteImportPayload(BaseModel):
    token: str
    pin_code: str


def create_app(service: Optional[SyncService] = None) -> FastAPI:
    svc = service or SyncService()
    static_dir = Path(__file__).parent / "static"

    app = FastAPI(
        title="Moult Диск — Защищённая синхронизация ПК и Android",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.sync_service = svc

    @app.get("/api/overview")
    def get_overview() -> Dict[str, Any]:
        return svc.get_dashboard_overview()

    @app.post("/api/role")
    def update_role(payload: RoleUpdatePayload) -> Dict[str, Any]:
        svc.cm.apply_role_preset(payload.role, payload.custom_name)
        svc.daemon.write_config_files(svc.cm.config, svc.cm.cert_path, svc.cm.key_path)
        return svc.get_dashboard_overview()

    @app.post("/api/folders")
    def create_or_update_folder(payload: FolderUpdatePayload) -> Dict[str, Any]:
        data = {k: v for k, v in payload.model_dump().items() if v is not None}
        folder = svc.add_or_update_folder(data)
        return {
            "folder": folder.model_dump(),
            "overview": svc.get_dashboard_overview(),
        }

    @app.post("/api/folders/{folder_id}/sync")
    def sync_single_folder(folder_id: str) -> Dict[str, Any]:
        try:
            res = svc.sync_folder(folder_id)
            return {
                "result": res,
                "overview": svc.get_dashboard_overview(),
            }
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/sync-all")
    def sync_all() -> Dict[str, Any]:
        res = svc.sync_all_folders()
        return {
            "result": res,
            "overview": svc.get_dashboard_overview(),
        }

    @app.get("/api/folders/{folder_id}/files")
    def list_files(folder_id: str) -> Dict[str, Any]:
        try:
            local_files = svc.list_folder_files(folder_id, side="local")
            remote_files = svc.list_folder_files(folder_id, side="remote")
            folder = next(f for f in svc.cm.config.folders if f.id == folder_id)
            return {
                "folder": folder.model_dump(),
                "local_files": local_files,
                "remote_files": remote_files,
            }
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/folders/{folder_id}/files")
    def create_file(folder_id: str, payload: FileCreatePayload) -> Dict[str, Any]:
        try:
            res = svc.create_or_upload_file(
                folder_id=folder_id,
                file_name=payload.file_name,
                content=payload.content_text.encode("utf-8"),
                side=payload.side,
                encrypt_passphrase=payload.encrypt_passphrase or None,
            )
            return {
                "created": res,
                "local_files": svc.list_folder_files(folder_id, side="local"),
                "remote_files": svc.list_folder_files(folder_id, side="remote"),
                "overview": svc.get_dashboard_overview(),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/folders/{folder_id}/open-explorer")
    def open_folder_in_explorer(folder_id: str) -> Dict[str, Any]:
        folder = next((f for f in svc.cm.config.folders if f.id == folder_id), None)
        if not folder:
            raise HTTPException(status_code=404, detail="Папка не найдена")
        return open_in_file_manager(folder.path)

    @app.post("/api/versions/{version_id}/restore")
    def restore_version(version_id: str) -> Dict[str, Any]:
        try:
            res = svc.restore_version(version_id)
            return {
                "restored": res,
                "overview": svc.get_dashboard_overview(),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/security/settings")
    def update_security(payload: SecurityUpdatePayload) -> Dict[str, Any]:
        sec = svc.cm.config.security
        for k, v in payload.model_dump().items():
            if v is not None and hasattr(sec, k):
                setattr(sec, k, v)
        if payload.profile == SecurityProfile.MAXIMUM_DIRECT:
            sec.relays_enabled = False
            sec.global_discovery_enabled = False
        if payload.autostart_windows is not None:
            set_windows_autostart(payload.autostart_windows)
        svc.cm.save()
        svc.daemon.write_config_files(svc.cm.config, svc.cm.cert_path, svc.cm.key_path)
        return svc.get_dashboard_overview()

    @app.post("/api/security/validate-device-id")
    def check_device_id(payload: DeviceIdCheckPayload) -> Dict[str, Any]:
        valid, message = validate_device_id(payload.device_id)
        return {"valid": valid, "result": message}

    @app.post("/api/security/pairing-invite")
    def create_pairing_invite(payload: PairingInviteCreatePayload) -> Dict[str, Any]:
        try:
            return svc.generate_encrypted_pairing_invite(payload.pin_code, payload.target_role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/security/import-invite")
    def import_pairing_invite(payload: PairingInviteImportPayload) -> Dict[str, Any]:
        try:
            res = svc.import_encrypted_pairing_invite(payload.token, payload.pin_code)
            return {
                "imported": res,
                "overview": svc.get_dashboard_overview(),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/devices")
    def add_device(payload: AddDevicePayload) -> Dict[str, Any]:
        try:
            peer = svc.add_peer_device(payload.name, payload.device_id, payload.role)
            return {
                "device": peer.model_dump(),
                "overview": svc.get_dashboard_overview(),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def serve_index() -> FileResponse:
        return FileResponse(str(static_dir / "index.html"))

    return app
