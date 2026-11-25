"""Authenticated encryption for provider secrets stored in the settings database."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import os
import secrets
from ctypes import wintypes

from cryptography.fernet import Fernet, InvalidToken

from src.core.config import get_settings


FERNET_PREFIX = "fernet:"
LOCAL_FERNET_PREFIX = "local-fernet:"
DPAPI_PREFIX = "dpapi:"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("This API key uses legacy Windows encryption. Re-enter and save the key on this installation.")

    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if protect:
        crypt = crypt32.CryptProtectData
        crypt.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
                          wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
        crypt.restype = wintypes.BOOL
        ok = crypt(
            ctypes.byref(source), "GTA CLIP COMMAND API key", None, None, None, 0x1, ctypes.byref(output)
        )
    else:
        crypt = crypt32.CryptUnprotectData
        crypt.argtypes = [ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
                          wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
        crypt.restype = wintypes.BOOL
        ok = crypt(
            ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
        )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        local_free = kernel32.LocalFree
        local_free.argtypes = [wintypes.HLOCAL]
        local_free.restype = wintypes.HLOCAL
        local_free(ctypes.cast(output.pbData, wintypes.HLOCAL))


def _fernet(master_key: str) -> Fernet:
    derived_key = hashlib.sha256(b"gta-clip-command:api-key:v1\0" + master_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived_key))


def _local_master_key() -> str:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite"):
        raise RuntimeError("CLOUD_KEY_ENCRYPTION_KEY is required to securely store provider API keys.")

    key_path = settings.data_dir / ".cloud_key_encryption_key"
    try:
        file_descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        master_key = key_path.read_text(encoding="utf-8")
    else:
        master_key = secrets.token_urlsafe(32)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as key_file:
            key_file.write(master_key)
            key_file.flush()
            os.fsync(key_file.fileno())
    if not master_key:
        raise RuntimeError("The local API-key encryption key is empty. Restore it or re-enter the API key.")
    if os.name != "nt":
        os.chmod(key_path, 0o600)
    return master_key


def encrypt_secret(value: str) -> str:
    master_key = os.environ.get("CLOUD_KEY_ENCRYPTION_KEY")
    if not master_key and os.name == "nt":
        encrypted = _crypt(value.encode("utf-8"), protect=True)
        return DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")

    prefix = FERNET_PREFIX
    if not master_key:
        master_key = _local_master_key()
        prefix = LOCAL_FERNET_PREFIX
    token = _fernet(master_key).encrypt(value.encode("utf-8")).decode("ascii")
    return prefix + token


def decrypt_secret(value: str) -> str:
    if value.startswith(LOCAL_FERNET_PREFIX):
        master_key = _local_master_key()
        prefix = LOCAL_FERNET_PREFIX
    elif value.startswith(FERNET_PREFIX):
        master_key = os.environ.get("CLOUD_KEY_ENCRYPTION_KEY")
        if not master_key:
            raise RuntimeError("CLOUD_KEY_ENCRYPTION_KEY is required to decrypt this API key.")
        prefix = FERNET_PREFIX
    elif value.startswith(DPAPI_PREFIX):
        payload = base64.b64decode(value.removeprefix(DPAPI_PREFIX), validate=True)
        return _crypt(payload, protect=False).decode("utf-8")
    else:
        raise ValueError("Stored secret is not in a supported encrypted format.")

    try:
        return _fernet(master_key).decrypt(value.removeprefix(prefix).encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Stored API key cannot be decrypted with the current encryption key.") from exc


def has_usable_secret(value: str) -> bool:
    if not value:
        return False
    try:
        decrypt_secret(value)
    except Exception:
        return False
    return True
