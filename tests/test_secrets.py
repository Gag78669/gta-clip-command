import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.secrets import decrypt_secret, encrypt_secret, has_usable_secret


def test_secret_round_trip_uses_authenticated_encryption(monkeypatch):
    monkeypatch.setenv("CLOUD_KEY_ENCRYPTION_KEY", "test-encryption-key-with-sufficient-entropy")
    secret = "provider-key-for-test-only"

    encrypted = encrypt_secret(secret)

    assert encrypted.startswith("fernet:")
    assert encrypted != secret
    assert decrypt_secret(encrypted) == secret
    assert has_usable_secret(encrypted)


def test_secret_cannot_be_decrypted_after_master_key_changes(monkeypatch):
    monkeypatch.setenv("CLOUD_KEY_ENCRYPTION_KEY", "first-encryption-key")
    encrypted = encrypt_secret("provider-key-for-test-only")
    monkeypatch.setenv("CLOUD_KEY_ENCRYPTION_KEY", "different-encryption-key")

    assert not has_usable_secret(encrypted)
    with pytest.raises(ValueError, match="current encryption key"):
        decrypt_secret(encrypted)


def test_sqlite_uses_private_local_key_when_no_master_key_is_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUD_KEY_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(
        "src.core.secrets.get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path, database_url="sqlite:///test.db"),
    )

    encrypted = encrypt_secret("provider-key-for-test-only")
    monkeypatch.setenv("CLOUD_KEY_ENCRYPTION_KEY", "later-added-project-key")

    assert encrypted.startswith("local-fernet:")
    assert decrypt_secret(encrypted) == "provider-key-for-test-only"
    if os.name != "nt":
        assert (tmp_path / ".cloud_key_encryption_key").stat().st_mode & 0o777 == 0o600


def test_non_sqlite_storage_requires_master_key(monkeypatch):
    monkeypatch.delenv("CLOUD_KEY_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(
        "src.core.secrets.get_settings",
        lambda: SimpleNamespace(data_dir=Path("/tmp"), database_url="postgresql://db"),
    )

    with pytest.raises(RuntimeError, match="CLOUD_KEY_ENCRYPTION_KEY"):
        encrypt_secret("provider-key-for-test-only")
