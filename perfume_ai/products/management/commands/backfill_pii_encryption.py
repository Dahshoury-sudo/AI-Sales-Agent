"""Re-save existing orders and carts so their PII is encrypted at rest.

The 0032 migration switched the phone and address columns to encrypted fields, but a
migration cannot encrypt what is already there: `decrypt_value` returns a non-ciphertext
value unchanged, so legacy rows keep reading correctly while staying plaintext in the
database. Only a write through the field encrypts them.

Must be a read-and-save loop, not a SQL UPDATE or a queryset .update(): both bypass
`get_prep_value`, which is where encryption happens, and would write plaintext straight
back. Order.save() also refreshes customer_phone_hash, which the analytics DISTINCT and
the admin phone search depend on.

Idempotent — re-encrypting an already-encrypted row is a no-op in effect, since the value
is decrypted on read before being encrypted again on write.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from products.models import Cart, Order
from products.encryption import _get_fernet


class Command(BaseCommand):
    help = "Encrypt existing Order and Cart PII in place and populate the phone blind index."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size", type=int, default=200,
            help="Rows per transaction (default 200).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be rewritten without writing.",
        )

    def handle(self, *args, **options):
        # Two separate jobs happen in the same pass: encrypting the PII columns, which
        # needs a key, and populating customer_phone_hash, which does not. Missing key
        # therefore warns rather than aborts — the analytics DISTINCT and the admin
        # phone search both run off that hash, and every row predating migration 0032
        # has an empty one, so unique_customers silently undercounts until this runs.
        encrypting = _get_fernet() is not None
        if not encrypting:
            self.stderr.write(self.style.WARNING(
                "FIELD_ENCRYPTION_KEY is not set or invalid, so nothing will actually be "
                "encrypted — this pass will only populate the phone blind index.\n"
                "Generate a key with:\n"
                '  python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"\n'
                "set it in the environment, and run this again to encrypt as well.\n"
                "Note the same key protects the Meta access tokens, which are therefore "
                "also stored in plaintext right now.\n"
            ))
        else:
            self.stdout.write("Encrypting PII and populating the phone blind index.")

        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        for model, label in ((Order, "orders"), (Cart, "carts")):
            total = model.objects.count()
            if not total:
                self.stdout.write(f"No {label} to rewrite.")
                continue

            if dry_run:
                self.stdout.write(f"Would rewrite {total} {label}.")
                continue

            done = 0
            ids = list(model.objects.values_list("id", flat=True))
            for start in range(0, len(ids), batch_size):
                chunk = ids[start:start + batch_size]
                with transaction.atomic():
                    for row in model.objects.filter(id__in=chunk):
                        # Full save, not update_fields: every encrypted column has to
                        # pass through get_prep_value to be rewritten.
                        row.save()
                        done += 1
                self.stdout.write(f"  {label}: {done}/{total}")

            self.stdout.write(self.style.SUCCESS(f"Rewrote {done} {label}."))
