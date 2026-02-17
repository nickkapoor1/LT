"""
Encryption utilities for storing account credentials at rest.

Uses Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256).
A random key is generated on first run and saved to data/.key.
"""

import json
from pathlib import Path

from cryptography.fernet import Fernet

from app.config.settings import ACCOUNTS_FILE, KEY_FILE


def _get_or_create_key() -> bytes:
    """Return the Fernet key, creating one on first run."""
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    # Restrict permissions so only the owner can read the key
    KEY_FILE.chmod(0o600)
    return key


def _fernet() -> Fernet:
    return Fernet(_get_or_create_key())


# ------------------------------------------------------------------
# Public helpers
# ------------------------------------------------------------------

def load_accounts() -> list[dict]:
    """
    Load and decrypt the accounts list.

    Returns a list of dicts, each with:
        email, password, club_slug, club_name, session_types, enabled
    """
    if not ACCOUNTS_FILE.exists():
        return []
    try:
        raw = _fernet().decrypt(ACCOUNTS_FILE.read_bytes())
        return json.loads(raw)
    except Exception:
        # If decryption fails (key changed, file corrupt), return empty
        return []


def save_accounts(accounts: list[dict]) -> None:
    """Encrypt and persist the accounts list."""
    raw = json.dumps(accounts, indent=2).encode()
    ACCOUNTS_FILE.write_bytes(_fernet().encrypt(raw))
    ACCOUNTS_FILE.chmod(0o600)


def add_account(
    email: str,
    password: str,
    club_slug: str,
    club_name: str,
    session_types: list[str] | None = None,
) -> list[dict]:
    """Add a new account (or update if email already exists). Returns updated list."""
    accounts = load_accounts()
    # Update existing or append new
    for acct in accounts:
        if acct["email"].lower() == email.lower():
            acct.update(
                password=password,
                club_slug=club_slug,
                club_name=club_name,
                session_types=session_types or [],
                enabled=True,
            )
            save_accounts(accounts)
            return accounts

    accounts.append({
        "email": email,
        "password": password,
        "club_slug": club_slug,
        "club_name": club_name,
        "session_types": session_types or [],
        "enabled": True,
    })
    save_accounts(accounts)
    return accounts


def remove_account(email: str) -> list[dict]:
    """Remove an account by email. Returns updated list."""
    accounts = [a for a in load_accounts() if a["email"].lower() != email.lower()]
    save_accounts(accounts)
    return accounts
