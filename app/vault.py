"""Protection des données sensibles (clés de licence, mots de passe Wi-Fi).

Principe : une paire de clés X25519 créée une fois.
- La clé publique reste dans la configuration du PC : chaque sauvegarde, même automatique,
  chiffre sans rien demander.
- La clé privée n'est jamais écrite en clair : elle est enveloppée deux fois (AES-GCM), par le
  mot de passe et par la clé de secours, toutes deux dérivées avec scrypt. Cette enveloppe
  (« coffre ») est copiée dans chaque version pour pouvoir la restaurer sur un autre PC.

La liste des licences saisie dans l'application reste lisible au quotidien sur ce PC :
elle est stockée localement, protégée par la session Windows (DPAPI).
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import hashlib
import io
import json
import os
import secrets
import zipfile
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VAULT_FILE = "coffre.json"
LICENSES_BLOB = "licences.chiffre"
WIFI_BLOB = "wifi.chiffre"
MAGIC = b"SPCENC1\n"
MIN_PASSWORD = 8
# 32 symboles sans caractères ambigus (0/O, 1/I/L) : 24 caractères = 120 bits.
RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
SCRYPT = dict(n=2**15, r=8, p=1)
_WRAP_AAD = b"SauvegardePC coffre v1"


class VaultError(Exception):
    """Mot de passe ou clé de secours incorrect, coffre ou fichier chiffré invalide."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _derive(secret: str, salt: bytes, params: dict) -> bytes:
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=params["n"], r=params["r"], p=params["p"],
                          maxmem=128 * 1024 * 1024, dklen=32)


def _wrap(secret: str, raw: bytes) -> dict:
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = _derive(secret, salt, SCRYPT)
    return dict(kdf="scrypt", **SCRYPT, salt=_b64(salt), nonce=_b64(nonce),
                ciphertext=_b64(AESGCM(key).encrypt(nonce, raw, _WRAP_AAD)))


def _unwrap(secret: str, wrapped: dict) -> bytes:
    key = _derive(secret, _unb64(wrapped["salt"]), wrapped)
    try:
        return AESGCM(key).decrypt(_unb64(wrapped["nonce"]), _unb64(wrapped["ciphertext"]), _WRAP_AAD)
    except InvalidTag:
        raise VaultError("Mot de passe ou clé de secours incorrect.") from None


def normalize_recovery(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def format_recovery(code: str) -> str:
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise VaultError(f"Le mot de passe doit contenir au moins {MIN_PASSWORD} caractères.")


def create(password: str) -> tuple[dict, str]:
    """Nouveau coffre. Renvoie (coffre, clé de secours à afficher une seule fois)."""
    check_password(password)
    private = X25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    recovery = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(24))
    doc = dict(format=1, created=datetime.now().isoformat(timespec="seconds"),
               public_key=_b64(_public_bytes(private.public_key())),
               password=_wrap(password, raw), recovery=_wrap(recovery, raw))
    return doc, format_recovery(recovery)


def _public_bytes(public: X25519PublicKey) -> bytes:
    return public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def unlock(doc: dict, secret: str) -> X25519PrivateKey:
    """Ouvre le coffre avec le mot de passe ou la clé de secours."""
    if not isinstance(doc, dict) or doc.get("format") != 1:
        raise VaultError("Coffre de protection absent ou illisible.")
    attempts = [("password", secret)]
    code = normalize_recovery(secret)
    if len(code) == 24:
        attempts.append(("recovery", code))
    for field, value in attempts:
        try:
            private = X25519PrivateKey.from_private_bytes(_unwrap(value, doc[field]))
        except VaultError:
            continue
        if _b64(_public_bytes(private.public_key())) != doc["public_key"]:
            raise VaultError("Coffre de protection incohérent.")
        return private
    raise VaultError("Mot de passe ou clé de secours incorrect.")


def change_password(doc: dict, secret: str, new_password: str) -> dict:
    """Nouveau mot de passe, après ouverture par l'ancien ou par la clé de secours (qui reste valable)."""
    check_password(new_password)
    private = unlock(doc, secret)
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    return dict(doc, password=_wrap(new_password, raw))


# --------------------------------------------------------------------------- #
# Chiffrement des données (clé publique seule : aucun mot de passe demandé)
# --------------------------------------------------------------------------- #

def _payload_key(shared: bytes, ephemeral: bytes, recipient: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"SauvegardePC donnees v1" + ephemeral + recipient).derive(shared)


def encrypt(doc: dict, data: bytes) -> bytes:
    recipient = _unb64(doc["public_key"])
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = _public_bytes(ephemeral.public_key())
    key = _payload_key(ephemeral.exchange(X25519PublicKey.from_public_bytes(recipient)), ephemeral_public, recipient)
    nonce = secrets.token_bytes(12)
    header = MAGIC + ephemeral_public + nonce
    return header + AESGCM(key).encrypt(nonce, data, header)


def decrypt(private: X25519PrivateKey, blob: bytes) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 44 + 16:
        raise VaultError("Fichier chiffré invalide.")
    ephemeral_public = blob[len(MAGIC):len(MAGIC) + 32]
    nonce = blob[len(MAGIC) + 32:len(MAGIC) + 44]
    recipient = _public_bytes(private.public_key())
    key = _payload_key(private.exchange(X25519PublicKey.from_public_bytes(ephemeral_public)), ephemeral_public, recipient)
    try:
        return AESGCM(key).decrypt(nonce, blob[len(MAGIC) + 44:], blob[:len(MAGIC) + 44])
    except InvalidTag:
        raise VaultError("Ce fichier a été chiffré avec un autre mot de passe ou il est endommagé.") from None


def write_blob(path: Path, doc: dict, data: bytes) -> None:
    """Écrit un fichier chiffré et le coffre de la version (pour la restaurer ailleurs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(encrypt(doc, data))
    os.replace(temporary, path)
    write_vault(path.parent.parent / VAULT_FILE, doc)


def write_vault(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def read_vault(folder: Path) -> dict | None:
    try:
        doc = json.loads((Path(folder) / VAULT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("format") == 1 else None


def zip_files(paths) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, Path(path).name)
    return buffer.getvalue()


def unzip_to(data: bytes, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            target = folder / Path(name).name  # jamais de chemin relatif dans l'archive
            target.write_bytes(archive.read(name))
            extracted.append(target)
    return extracted


def scrub(path: Path) -> None:
    """Écrase puis supprime un fichier en clair (au mieux : un SSD peut garder d'anciens blocs)."""
    try:
        info = path.stat()
        size = info.st_size if info.st_nlink == 1 else 0  # lien physique : ne pas vider l'autre version
        with open(path, "r+b") as stream:
            stream.write(b"\0" * size)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Liste locale des licences, protégée par la session Windows (DPAPI)
# --------------------------------------------------------------------------- #

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    source = _Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    result = _Blob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN : ne jamais afficher de fenêtre (sauvegarde automatique).
    if not function(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
        raise VaultError("Protection Windows indisponible pour la liste des licences.")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def load_local_licenses(path: Path) -> list[dict]:
    try:
        data = json.loads(_dpapi(Path(path).read_bytes(), protect=False).decode("utf-8"))
    except (OSError, ValueError, VaultError):
        return []
    return data if isinstance(data, list) else []


def save_local_licenses(path: Path, licenses: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_dpapi(json.dumps(licenses, ensure_ascii=False).encode("utf-8"), protect=True))
    os.replace(temporary, path)
