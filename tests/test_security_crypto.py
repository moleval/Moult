from __future__ import annotations

from pathlib import Path
import pytest

from moult.security.certificates import (
    ensure_device_identity,
    validate_device_id,
)
from moult.security.crypto import (
    compute_file_sha256,
    create_encrypted_pairing_token,
    decrypt_bytes_aes_gcm,
    decrypt_file_aes_gcm,
    decrypt_pairing_token,
    encrypt_bytes_aes_gcm,
    encrypt_file_aes_gcm,
)


def test_certificate_and_luhn_device_id(tmp_path: Path) -> None:
    device_id, cert_path, key_path = ensure_device_identity(tmp_path / "certs")
    assert cert_path.exists()
    assert key_path.exists()

    valid, normalized = validate_device_id(device_id)
    assert valid is True
    assert normalized == device_id
    assert len(device_id.split("-")) == 8

    # Проверяем, что искажение даже одного символа ловится контрольной суммой Luhn mod 32
    last_char = device_id[-1]
    replacement = "A" if last_char != "A" else "B"
    tampered_id = device_id[:-1] + replacement
    valid_tampered, err_msg = validate_device_id(tampered_id)
    assert valid_tampered is False
    assert "контрольной суммы" in err_msg


def test_aes_256_gcm_bytes_and_files(tmp_path: Path) -> None:
    secret_data = "Конфиденциальный отчёт для слива с Рабочего ПК на Главный ПК".encode("utf-8")
    passphrase = "StrongPassword#2026"

    blob = encrypt_bytes_aes_gcm(secret_data, passphrase)
    assert blob.startswith(b"MOULT_ENC_V1")
    restored = decrypt_bytes_aes_gcm(blob, passphrase)
    assert restored == secret_data

    # Неверный пароль должен вызывать ошибку проверки подлинности GCM Tag
    with pytest.raises(ValueError, match="Ошибка проверки подлинности"):
        decrypt_bytes_aes_gcm(blob, "WrongPassword")

    # Проверка файлового шифрования
    src_file = tmp_path / "plain.txt"
    enc_file = tmp_path / "plain.txt.moultenc"
    dec_file = tmp_path / "decrypted.txt"
    src_file.write_bytes(secret_data)

    meta = encrypt_file_aes_gcm(src_file, enc_file, passphrase)
    assert enc_file.exists()
    assert meta["plain_sha256"] == compute_file_sha256(src_file)

    dec_meta = decrypt_file_aes_gcm(enc_file, dec_file, passphrase)
    assert dec_file.read_bytes() == secret_data
    assert dec_meta["sha256"] == meta["plain_sha256"]


def test_encrypted_pairing_token() -> None:
    payload = {
        "host_device_id": "ABCDEFG-HIJKLMN-OPQRSTU-VWXYZ23-4567ABC-DEFGHIJ-KLMNOPQ-RSTUVWX",
        "host_device_name": "Главный ПК (Дом)",
        "tls_version": "TLSv1.3",
    }
    token = create_encrypted_pairing_token(payload, "PinCode9988")
    assert token.startswith("MOULT-PAIR-V1:")

    unpacked = decrypt_pairing_token(token, "PinCode9988")
    assert unpacked == payload

    with pytest.raises(ValueError):
        decrypt_pairing_token(token, "WrongPin")
