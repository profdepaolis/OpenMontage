"""Configuration, paths, and local secret management for KORA STUDIO."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=False)

ASSETS_KORA_DIR = REPO_ROOT / "assets" / "kora"
DATA_DIR = Path(os.environ.get("KORA_DATA_DIR", str(REPO_ROOT / "kora_data"))).resolve()
VAULT_DIR = DATA_DIR / "vault"
VAULT_ORIGINALS_DIR = VAULT_DIR / "originals"
VAULT_WORKING_DIR = VAULT_DIR / "working"
VAULT_THUMBS_DIR = VAULT_DIR / "thumbs"
SOUND_LIBRARY_DIR = DATA_DIR / "sound_library"
PROJECTS_DIR = DATA_DIR / "projects"
EXPORTS_DIR = DATA_DIR / "exports"
BACKUPS_DIR = DATA_DIR / "backups"
CACHE_DIR = DATA_DIR / "cache"
VOICES_PIPER_DIR = DATA_DIR / "voices" / "piper"
DB_PATH = Path(os.environ.get("KORA_DB_PATH", str(DATA_DIR / "kora_studio.db"))).resolve()
MASTER_KEY_PATH = Path(
    os.environ.get("KORA_MASTER_KEY_PATH", str(REPO_ROOT / ".kora_master.key"))
).resolve()

DEFAULT_FPS = 25
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_PAID_BUDGET_EUR = 0.0
MAX_MONTHLY_CAP_EUR = 20.0


def ensure_directories() -> None:
    """Create all required runtime workspace directories safely."""
    for d in (
        DATA_DIR,
        VAULT_DIR,
        VAULT_ORIGINALS_DIR,
        VAULT_WORKING_DIR,
        VAULT_THUMBS_DIR,
        SOUND_LIBRARY_DIR,
        PROJECTS_DIR,
        EXPORTS_DIR,
        BACKUPS_DIR,
        CACHE_DIR,
        VOICES_PIPER_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


class SecretVault:
    """Authenticated local encryption for API keys stored outside source code.

    Uses a locally generated 256-bit master key (.kora_master.key, chmod 0600)
    with SHA-256 CTR stream + HMAC-SHA256 authentication tag so secrets are
    never persisted in plaintext in SQLite or config files.
    """

    def __init__(self, key_path: Optional[Path] = None) -> None:
        self.key_path = Path(key_path) if key_path else MASTER_KEY_PATH

    def _load_or_create_key(self) -> bytes:
        env_key = os.environ.get("KORA_MASTER_KEY")
        if env_key:
            return hashlib.sha256(env_key.encode("utf-8")).digest()
        if self.key_path.is_file():
            raw = self.key_path.read_bytes().strip()
            try:
                decoded = base64.urlsafe_b64decode(raw)
                if len(decoded) >= 32:
                    return decoded[:32]
            except Exception:
                pass
            return hashlib.sha256(raw).digest()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        self.key_path.write_bytes(base64.urlsafe_b64encode(key))
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def _keystream(self, enc_key: bytes, nonce: bytes, length: int) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < length:
            block = hashlib.sha256(enc_key + nonce + counter.to_bytes(8, "big")).digest()
            out.extend(block)
            counter += 1
        return bytes(out[:length])

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        master = self._load_or_create_key()
        enc_key = hashlib.sha256(master + b"kora-enc").digest()
        mac_key = hashlib.sha256(master + b"kora-mac").digest()
        nonce = secrets.token_bytes(16)
        data = plaintext.encode("utf-8")
        stream = self._keystream(enc_key, nonce, len(data))
        ct = bytes(a ^ b for a, b in zip(data, stream))
        tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
        return "v1:" + base64.urlsafe_b64encode(nonce + tag + ct).decode("ascii")

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        if not token.startswith("v1:"):
            raise ValueError("Unsupported secret token format")
        blob = base64.urlsafe_b64decode(token[3:].encode("ascii"))
        if len(blob) < 48:
            raise ValueError("Corrupted secret token")
        nonce = blob[:16]
        tag = blob[16:48]
        ct = blob[48:]
        master = self._load_or_create_key()
        enc_key = hashlib.sha256(master + b"kora-enc").digest()
        mac_key = hashlib.sha256(master + b"kora-mac").digest()
        expected = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("Secret integrity verification failed")
        stream = self._keystream(enc_key, nonce, len(ct))
        pt = bytes(a ^ b for a, b in zip(ct, stream))
        return pt.decode("utf-8")

    @staticmethod
    def mask(secret: str) -> str:
        if not secret:
            return ""
        if len(secret) <= 8:
            return "*" * len(secret)
        return f"{secret[:4]}...{secret[-4:]}"
