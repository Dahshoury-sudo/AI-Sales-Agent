import base64
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
