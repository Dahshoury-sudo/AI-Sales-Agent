import json

data = json.load(open('eval_harness/results/verdicts.json', 'r', encoding='utf-8'))
findings = json.load(open('eval_harness/results/findings.json', 'r', encoding='utf-8'))

# Score averages by dimension
dims = {}
for v in data:
    for k, s in v['scores'].items():
        if s is not None:
            dims.setdefault(k, []).append(s)

print('=== SCORES BY DIMENSION ===')
for k in sorted(dims, key=lambda x: sum(dims[x])/len(dims[x])):
    avg = sum(dims[k])/len(dims[k])
    print(f'  {k:30s} {avg:.1f}/10  (n={len(dims[k])})')

# Worst scenarios
print()
print('=== WORST SCENARIOS ===')
for v in sorted(data, key=lambda x: sum(s for s in x['scores'].values() if s is not None)/max(1,sum(1 for s in x['scores'].values() if s is not None)))[:10]:
    scores = [s for s in v['scores'].values() if s is not None]
    avg = sum(scores)/len(scores)
    high_fails = len([f for f in v.get('failures', []) if f['severity'] == 'high'])
    print(f"  {v['id']:5s} ({v['category']:15s}) avg={avg:.1f}  high_fails={high_fails}")

# Best scenarios
print()
print('=== BEST SCENARIOS ===')
for v in sorted(data, key=lambda x: sum(s for s in x['scores'].values() if s is not None)/max(1,sum(1 for s in x['scores'].values() if s is not None)), reverse=True)[:5]:
    scores = [s for s in v['scores'].values() if s is not None]
    avg = sum(scores)/len(scores)
    print(f"  {v['id']:5s} ({v['category']:15s}) avg={avg:.1f}")

# High severity failure categories
print()
print('=== HIGH SEVERITY FAILURES BY CATEGORY ===')
cats = {}
for v in data:
    for f in v.get('failures', []):
        if f['severity'] == 'high':
            cats[f['category']] = cats.get(f['category'], 0) + 1
for c in sorted(cats, key=cats.get, reverse=True):
    print(f'  {c:30s} {cats[c]}x')

# Deterministic findings summary
print()
print('=== DETERMINISTIC FINDINGS SUMMARY ===')
codes = {}
for sid, flist in findings.items():
    for f in flist:
        key = f['code']
        codes.setdefault(key, {'count': 0, 'high': 0, 'scenarios': []})
        codes[key]['count'] += 1
        if f['severity'] == 'high':
            codes[key]['high'] += 1
        codes[key]['scenarios'].append(sid)

for code in sorted(codes, key=lambda x: codes[x]['count'], reverse=True):
    info = codes[code]
    print(f"  {code:35s} {info['count']}x  ({info['high']} high)  in: {', '.join(info['scenarios'][:8])}")

# M1 (Sauvage similarity) detail
print()
print('=== M1 DETAIL (Sauvage similarity scenario) ===')
for v in data:
    if v['id'] == 'M1':
        print(f"  Scores: {v['scores']}")
        print(f"  What worked:")
        for w in v.get('what_worked', []):
            print(f"    + {w[:120]}...")
        print(f"  Failures:")
        for f in v.get('failures', []):
            print(f"    [{f['severity']}] {f['category']}: {f['what'][:120]}")
        print(f"  Verdict: {v.get('salesperson_verdict', '')[:200]}")
