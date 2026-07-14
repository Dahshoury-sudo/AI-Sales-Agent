import json

with open("test_results.json", encoding="utf-8") as f:
    data = json.load(f)

for d in data:
    eval_dict = d.get("evaluation", {})
    score = eval_dict.get("score", "N/A")
    notes = eval_dict.get("notes", "No notes")
    persona = d.get("persona", "Unknown persona")
    print(f"Convo {d['id']} | Score: {score}/10 | {notes}")
    print(f"Persona: {persona}\n")
