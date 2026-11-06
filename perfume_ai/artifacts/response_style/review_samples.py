"""Summarize paired isolated evaluations without treating length as correctness."""
import json
from pathlib import Path
import re
from statistics import mean

root = Path(__file__).resolve().parent
before = {row['id']: row for row in json.loads((root / 'live_before/runs.json').read_text(encoding='utf-8'))}
after = {row['id']: row for row in json.loads((root / 'live_after/runs.json').read_text(encoding='utf-8'))}
rows = []
for key in sorted(before.keys() & after.keys()):
    old, new = before[key], after[key]
    for previous, current in zip(old['turns'], new['turns']):
        words_before = len(previous['reply'].split())
        words_after = len(current['reply'].split())
        rows.append({'scenario': key, 'turn': current['n'], 'customer': current['user'],
                     'before': previous['reply'], 'after': current['reply'],
                     'words_before': words_before, 'words_after': words_after,
                     'findings_before': previous['findings'], 'findings_after': current['findings']})
summary = {'scenarios': len(before.keys() & after.keys()), 'turns': len(rows),
           'mean_words_before': round(mean(row['words_before'] for row in rows), 1),
           'mean_words_after': round(mean(row['words_after'] for row in rows), 1),
           'findings_before': {key: before[key]['findings'] for key in before if before[key]['findings']},
           'findings_after': {key: after[key]['findings'] for key in after if after[key]['findings']}}
(root / 'comparison.json').write_text(json.dumps({'summary': summary, 'turns': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
for row in rows:
    print(f"\n{row['scenario']}/{row['turn']}: {row['words_before']} -> {row['words_after']} words")
    print(row['after'])
