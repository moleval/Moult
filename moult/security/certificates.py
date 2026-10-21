from __future__ import annotations

import base64
import datetime
import hashlib
from pathlib import Path
from typing import Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def _luhn_mod32_char(chunk: str) -> str:
    """
    Вычисляет контрольный символ Luhn mod 32 по стандарту Syncthing BEP (Block Exchange Protocol).
    """
    factor = 1
    total = 0
    n = 32
    for ch in chunk:
        code_point = BASE32_ALPHABET.index(ch)
        addend = factor * code_point
        factor = 2 if factor == 1 else 1
        addend = (addend // n) + (addend % n)
        total += addend
    remainder = total % n
    check_code_point = (n - remainder) % n
    return BASE32_ALPHABET[check_code_point]


def compute_syncthing_device_id(cert_der: bytes) -> str:
    """
    Вычисляет официальный Syncthing Device ID из DER-представления TLS сертификата:
    SHA-256 -> Base32 (52 символа) -> 4 блока по 13 + Luhn mod 32 -> 8 групп по 7 символов.
    """
    digest = hashlib.sha256(cert_der).digest()
    b32 = base64.b32encode(digest).decode("ascii").rstrip("=")
    if len(b32) != 52:
        raise ValueError(f"Некорректная длина Base32 дайджеста: {len(b32)}")

    with_check = []
    for i in range(0, 52, 13):
        chunk13 = b32[i : i + 13]
        check_ch = _luhn_mod32_char(chunk13)
        with_check.append(chunk13 + check_ch)

    full56 = "".join(with_check)
    groups = [full56[i : i + 7] for i in range(0, 56, 7)]
    return "-".join(groups)


def validate_device_id(device_id: str) -> Tuple[bool, str]:
    """
    Проверяет корректность и контрольные суммы Luhn mod 32 в Device ID (для ПК и Android).
    """
    cleaned = device_id.strip().upper().replace(" ", "").replace("-", "")
    if len(cleaned) != 56:
        return False, f"Ожидается 56 символов Base32 (получено {len(cleaned)})"

    for ch in cleaned:
        if ch not in BASE32_ALPHABET:
            return False, f"Недопустимый символ '{ch}' (разрешены только A-Z и 2-7)"

    for i in range(0, 56, 14):
        chunk14 = cleaned[i : i + 14]
        data13, check_char = chunk14[:13], chunk14[13]
        expected = _luhn_mod32_char(data13)
        if check_char != expected:
            return (
                False,
                f"Ошибка контрольной суммы в блоке {i // 14 + 1} (ожидался символ '{expected}', указан '{check_char}')",
            )

    formatted = "-".join(cleaned[i : i + 7] for i in range(0, 56, 7))
    return True, formatted


def ensure_device_identity(cert_dir: Path, common_name: str = "syncthing") -> Tuple[str, Path, Path]:
    """
    Создаёт (или загружает существующую) криптографическую пару ECDSA P-384 + X.509 сертификат
    для взаимной TLS 1.3 аутентификации (mTLS) между Главным ПК, Рабочим ПК и Android.
    Возвращает (device_id, cert_path, key_path).
    """
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "cert.pem"
    key_path = cert_dir / "key.pem"

    if cert_path.exists() and key_path.exists():
        cert_pem = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)
        cert_der = cert.public_bytes(serialization.Encoding.DER)
        device_id = compute_syncthing_device_id(cert_der)
        return device_id, cert_path, key_path

    private_key = ec.generate_private_key(ec.SECP384R1())
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Moult Sync Security"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(common_name), x509.DNSName("localhost")]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(private_key, hashes.SHA384())
    )

    key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_bytes = cert.public_bytes(serialization.Encoding.PEM)

    key_path.write_bytes(key_bytes)
    cert_path.write_bytes(cert_bytes)

    cert_der = cert.public_bytes(serialization.Encoding.DER)
    device_id = compute_syncthing_device_id(cert_der)
    return device_id, cert_path, key_path
