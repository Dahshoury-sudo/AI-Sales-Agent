import base64
import hashlib
import hmac
import logging
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)


def _get_fernet():
    """Get a Fernet instance using the FIELD_ENCRYPTION_KEY from settings."""
    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', '')
    if not key:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        logger.error("Invalid FIELD_ENCRYPTION_KEY. Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
        return None


def encrypt_value(value):
    """Encrypt a string value. Returns the original value if no key is configured."""
    if not value:
        return value
    fernet = _get_fernet()
    if not fernet:
        return value
    try:
        return fernet.encrypt(value.encode()).decode()
    except Exception as e:
        logger.error(f"Encryption error: {e}")
        return value


def decrypt_value(value):
    """Decrypt a string value. Returns the original value if decryption fails (e.g., not encrypted)."""
    if not value:
        return value
    fernet = _get_fernet()
    if not fernet:
        return value
    try:
        return fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        # Value is not encrypted (e.g., legacy data), return as-is
        return value
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return value


class EncryptedTextField(models.TextField):
    """A TextField that encrypts data at rest using Fernet."""

    def get_prep_value(self, value):
        """Encrypt before saving to DB."""
        value = super().get_prep_value(value)
        return encrypt_value(value)

    def from_db_value(self, value, expression, connection):
        """Decrypt when reading from DB."""
        return decrypt_value(value)


class EncryptedCharField(models.CharField):
    """A CharField that encrypts data at rest using Fernet."""

    def get_prep_value(self, value):
        """Encrypt before saving to DB."""
        value = super().get_prep_value(value)
        return encrypt_value(value)

    def from_db_value(self, value, expression, connection):
        """Decrypt when reading from DB."""
        return decrypt_value(value)


# ── Blind index ──────────────────────────────────────────────────────────────
#
# Fernet is non-deterministic: the same phone number encrypts differently every
# time. That is what you want at rest, but it silently breaks any query — an
# `icontains` search encrypts the search term into ciphertext that matches nothing,
# and a DISTINCT over the column counts every row as unique.
#
# A blind index is the standard answer: store a keyed, deterministic hash alongside
# the encrypted value and query that instead. It supports exact match only, which is
# enough for "find the order for this phone number" and for counting distinct
# customers. Substring search over ciphertext is not solvable this way, which is why
# customer_name is deliberately left unencrypted.


def normalize_phone(value):
    """Canonical form of a phone number for hashing.

    Load-bearing, and it fixes a pre-existing bug. `_looks_like_phone`
    (order_service.py) only counts digits, so numbers are stored exactly as the
    customer typed them — meaning the unique_customers KPI already counted
    "0100 000 0000" and "01000000000" as two different people before any encryption
    existed.

    Egyptian mobiles are 11 digits starting with 0, and the same line may arrive as
    +20 1x..., 0020 1x..., or 01x.... Taking the last 10 digits collapses all three
    onto one key.
    """
    if not value:
        return ""
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def phone_blind_index(value):
    """Deterministic keyed hash of a phone number, or "" if there is nothing to hash.

    Keyed with FIELD_ENCRYPTION_KEY so the hash cannot be brute-forced from a stolen
    database alone — the space of Egyptian mobile numbers is small enough that a plain
    SHA-256 would be trivially reversible.
    """
    normalized = normalize_phone(value)
    if not normalized:
        return ""

    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', '')
    if not key:
        # Mirrors encrypt_value: without a key this degrades instead of crashing, so a
        # misconfigured environment still functions. Grouping stays correct because the
        # hash is still deterministic.
        logger.warning("No FIELD_ENCRYPTION_KEY set; phone blind index is unkeyed.")
        key = "unkeyed"

    key_bytes = key.encode() if isinstance(key, str) else key
    return hmac.new(key_bytes, normalized.encode(), hashlib.sha256).hexdigest()
