"""The store-assigned scent vocabulary, shared by forms, APIs and imports."""

import re

from django.core.exceptions import ValidationError


SCENT_TAG_CHOICES = (
    ("fresh", "فريش"), ("sweet", "مسكّر"), ("oud", "عودي"),
    ("woody", "خشبي"), ("citrus", "حمضي"), ("floral", "زهري"),
    ("fruity", "فاكهي"), ("spicy", "متبّل"), ("musky", "مسكي"),
    ("clean", "نضيف"), ("powdery", "بودري"), ("smoky", "دخاني"),
    ("leather", "جلدي"), ("aquatic", "مائي"), ("light", "خفيف"),
    ("heavy", "تقيل"),
)
SCENT_TAG_LABELS = dict(SCENT_TAG_CHOICES)


def normalize_scent_tags(value):
    """Validate an API/model array; never silently turn a typo into a category."""
    if not isinstance(value, list):
        raise ValidationError("Scent labels must be a list.")
    result = []
    for tag in value:
        if not isinstance(tag, str) or tag.strip().lower() not in SCENT_TAG_LABELS:
            raise ValidationError("Unknown scent label: %(tag)s", params={"tag": str(tag)})
        tag = tag.strip().lower()
        if tag not in result:
            result.append(tag)
    return result


def parse_scent_tags_cell(value):
    if value is None or not str(value).strip():
        return []
    return normalize_scent_tags(re.split(r"[,،]", str(value)))


def validate_scent_tags(value):
    normalize_scent_tags(value)


def describe_scent_tags(tags):
    return "، ".join(SCENT_TAG_LABELS[tag] for tag in tags if tag in SCENT_TAG_LABELS)
