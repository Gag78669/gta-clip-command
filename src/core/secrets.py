"""Small Windows DPAPI wrapper for secrets stored in the local settings database."""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Encrypted API-key storage is currently supported on Windows only.")

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


def encrypt_secret(value: str) -> str:
    key = os.getenv("CLOUD_KEY_ENCRYPTION_KEY", "")
    if key:
        from cryptography.fernet import Fernet
        return "fernet:" + Fernet(key.encode("ascii")).encrypt(value.encode("utf-8")).decode("ascii")
    if os.name != "nt":
        raise RuntimeError("Set CLOUD_KEY_ENCRYPTION_KEY to encrypt cloud API keys on this server.")
    return "dpapi:" + base64.b64encode(_crypt(value.encode("utf-8"), protect=True)).decode("ascii")


def decrypt_secret(value: str) -> str:
    if value.startswith("fernet:"):
        key = os.getenv("CLOUD_KEY_ENCRYPTION_KEY", "")
        if not key:
            raise RuntimeError("CLOUD_KEY_ENCRYPTION_KEY is not configured.")
        from cryptography.fernet import Fernet
        return Fernet(key.encode("ascii")).decrypt(value.removeprefix("fernet:").encode("ascii")).decode("utf-8")
    if not value.startswith("dpapi:"):
        raise ValueError("Stored secret is not encrypted with Windows DPAPI.")
    payload = base64.b64decode(value.removeprefix("dpapi:"), validate=True)
    return _crypt(payload, protect=False).decode("utf-8")
