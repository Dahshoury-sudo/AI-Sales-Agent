"""Dump conversations with their injected context, which is what a diagnosis needs.

`dump799.py` saved role and content only. The bug in 798/799 is about *what data the model was
handed*, so `internal_context` is the half that matters.

    python dump_convs.py 798 799
"""
import json
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()

from products.models import Conversation  # noqa: E402

for raw in sys.argv[1:] or ["798", "799"]:
    conversation = Conversation.objects.filter(id=int(raw)).first()
    if not conversation:
        print(f"NO CONVERSATION {raw}")
        continue

    print(f"=== conversation {raw} | store={conversation.store} "
          f"| platform={conversation.platform} | created={conversation.created_at} ===")
    rows = [
        {
            "role": message.role,
            "content": message.content,
            "internal_context": message.internal_context,
            "created_at": str(message.created_at),
        }
        for message in conversation.messages.order_by("created_at")
    ]
    path = f"conv_{raw}_full.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
    print(f"wrote {path}, {len(rows)} messages")

    for index, row in enumerate(rows, start=1):
        print(f"\n--- [{index}] {row['role']} ---")
        print(row["content"])
        if row["role"] == "assistant":
            context = row["internal_context"] or ""
            print(f"    injected ({len(context)} chars):")
            for line in context.splitlines():
                if line.startswith("Name (") or line.startswith("Name:"):
                    print(f"      {line}")
