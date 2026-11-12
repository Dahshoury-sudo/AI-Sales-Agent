"""Print a compact view of one simulated conversation: python eval_harness/show_simulation.py <json> [--context]"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
record = json.load(open(sys.argv[1], encoding="utf-8"))
show_context = "--context" in sys.argv
print(f"== {record['id']} verdict={record.get('verdict')} ended_by={record.get('ended_by')} error={record.get('error')}")
for turn in record["turns"]:
    print(f"\n[{turn['n']}] CUSTOMER: {turn['user']}")
    print(f"    route={turn.get('classification')} stage={turn.get('stage')} cart={[(i['name'], i['volume'], i['quantity']) for i in turn['cart']['items']]} orders={len(turn['orders'])}")
    print(f"    prefs={json.dumps(turn.get('saved_preferences'), ensure_ascii=False)[:300]}")
    print("    SELLER: " + (turn["reply"] or "(none)").replace("\n", "\n            "))
    for finding in turn["findings"]:
        print(f"    !! {finding[1]} {finding[0]}: {finding[2][:160]}")
    if show_context and turn.get("context"):
        print("    CONTEXT: " + turn["context"][:1500].replace("\n", "\n             "))
judged = record.get("judge") or {}
print("\nSCORES", judged.get("scores"), judged.get("error") or "")
for item in judged.get("criteria") or []:
    print(f"  criterion met={item.get('met')}: {item.get('criterion', '')[:90]} | {item.get('evidence', '')[:160]}")
for issue in judged.get("issues") or []:
    print(f"  ISSUE t{issue.get('turn')} {issue.get('severity')} {issue.get('category')}: {issue.get('why', '')[:220]}")
for item in judged.get("deterministic_review") or []:
    print(f"  DET t{item.get('turn')} {item.get('code')} -> {item.get('verdict')}: {item.get('reason', '')[:160]}")
print("SUMMARY", judged.get("summary"))
print("REASONS", json.dumps(record.get("verdict_reasons"), ensure_ascii=False)[:1500])
print("USAGE", json.dumps({k: v for k, v in (record.get("usage") or {}).items() if k != "failed_calls"}, ensure_ascii=False))
