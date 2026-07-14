import json

with open("test_results.json", "r", encoding="utf-8") as f:
    data = json.load(f)

weak_ids = [1, 3, 12]

for d in data:
    if d["id"] in weak_ids:
        print(f"\n{'='*60}")
        print(f"CONV {d['id']} | Score: {d['evaluation'].get('score')}/10")
        print(f"Notes: {d['evaluation'].get('notes')}")
        for i, m in enumerate(d['history'][-6:]):
            role = 'USER' if m['role']=='user' else 'BOT '
            print(f"[{i}] {role}: {m['content'][:150]}")
