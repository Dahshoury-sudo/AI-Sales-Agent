import json
with open('test_results.json','r',encoding='utf-8') as f:
    data = json.load(f)
target = [1,3,7,12,18,20]
for d in data:
    if d['id'] in target:
        print(f"Conv {d['id']} ({d['evaluation'].get('score')}/10): {d['evaluation'].get('notes')}")
