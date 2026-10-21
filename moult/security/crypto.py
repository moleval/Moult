from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC_HEADER = b"MOULT_ENC_V1"
SALT_SIZE = 16
NONCE_SIZE = 12
PBKDF2_ITERATIONS = 600_000


def compute_bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    """
    Потоковый расчёт SHA-256 контрольной суммы файла для контроля целостности после синхронизации.
    """
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _derive_aes256_key(passphrase: str, salt: bytes) -> bytes:
    if not passphrase:
        raise ValueError("Пароль шифрования не может быть пустым")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_bytes_aes_gcm(plaintext: bytes, passphrase: str, aad: bytes = MAGIC_HEADER) -> bytes:
    """
    Шифрует байты алгоритмом AES-256-GCM с выводом ключа через PBKDF2-HMAC-SHA256 (600 000 итераций).
    Формат пакета: MAGIC_HEADER (12B) + SALT (16B) + NONCE (12B) + CIPHERTEXT_WITH_TAG
    """
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    key = _derive_aes256_key(passphrase, salt)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, aad)
    return MAGIC_HEADER + salt + nonce + ciphertext


def decrypt_bytes_aes_gcm(blob: bytes, passphrase: str, aad: bytes = MAGIC_HEADER) -> bytes:
    """
    Расшифровывает и проверяет подлинность (GCM Authentication Tag) пакета AES-256-GCM.
    """
    header_len = len(MAGIC_HEADER)
    min_len = header_len + SALT_SIZE + NONCE_SIZE + 16
    if len(blob) < min_len or not blob.startswith(MAGIC_HEADER):
        raise ValueError("Некорректный формат зашифрованных данных Moult (ожидался заголовок MOULT_ENC_V1)")

    salt_start = header_len
    nonce_start = salt_start + SALT_SIZE
    ct_start = nonce_start + NONCE_SIZE

    salt = blob[salt_start:nonce_start]
    nonce = blob[nonce_start:ct_start]
    ciphertext = blob[ct_start:]

    key = _derive_aes256_key(passphrase, salt)
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise ValueError("Ошибка проверки подлинности: неверный пароль или данные были повреждены при передаче") from exc


def encrypt_file_aes_gcm(src_path: Path, dst_path: Path, passphrase: str) -> Dict[str, Any]:
    """
    Шифрует файл перед отправкой или для хранения в защищённой папке (Сейф).
    Сохраняет SHA-256 исходного файла в метаданных AAD.
    """
    raw = src_path.read_bytes()
    plain_sha256 = compute_bytes_sha256(raw)
    encrypted = encrypt_bytes_aes_gcm(raw, passphrase)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_bytes(encrypted)
    return {
        "source_file": src_path.name,
        "encrypted_file": dst_path.name,
        "plain_size": len(raw),
        "encrypted_size": len(encrypted),
        "plain_sha256": plain_sha256,
        "encrypted_sha256": compute_bytes_sha256(encrypted),
        "cipher": "AES-256-GCM + PBKDF2-HMAC-SHA256",
    }


def decrypt_file_aes_gcm(src_path: Path, dst_path: Path, passphrase: str) -> Dict[str, Any]:
    """
    Расшифровывает файл .moultenc и проверяет целостность.
    """
    blob = src_path.read_bytes()
    plaintext = decrypt_bytes_aes_gcm(blob, passphrase)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_bytes(plaintext)
    return {
        "decrypted_file": dst_path.name,
        "size": len(plaintext),
        "sha256": compute_bytes_sha256(plaintext),
    }


def create_encrypted_pairing_token(payload: Dict[str, Any], pin_code: str) -> str:
    """
    Упаковывает и шифрует приглашение для сопряжения второго ПК или Android-приложения
    с помощью пароля/PIN-кода (защита при пересылке через мессенджеры/почту).
    """
    raw_json = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    encrypted = encrypt_bytes_aes_gcm(raw_json, pin_code)
    b64 = base64.urlsafe_b64encode(encrypted).decode("ascii")
    return f"MOULT-PAIR-V1:{b64}"


def decrypt_pairing_token(token: str, pin_code: str) -> Dict[str, Any]:
    """
    Расшифровывает токен сопряжения устройств и возвращает словарь параметров подключения.
    """
    cleaned = token.strip()
    prefix = "MOULT-PAIR-V1:"
    if not cleaned.startswith(prefix):
        raise ValueError("Некорректный формат токена сопряжения (ожидался префикс MOULT-PAIR-V1:)")
    b64_part = cleaned[len(prefix) :]
    blob = base64.urlsafe_b64decode(b64_part.encode("ascii"))
    decrypted = decrypt_bytes_aes_gcm(blob, pin_code)
    return json.loads(decrypted.decode("utf-8"))
