import json
import os

with open('test_results.json', encoding='utf-8') as f:
    data = json.load(f)

output_path = r"C:\Users\Mohamed\.gemini\antigravity-ide\brain\838b80d0-85b6-4a5c-80ca-175cf22ec7bb\walkthrough.md"

with open(output_path, 'w', encoding='utf-8') as f:
    f.write("# نتائج اختبار الضغط على البوت (Stress Test)\n\n")
    f.write("> [!NOTE]\n> لقد انتهى الاختبار الشامل الذي تم فيه محاكاة 20 عميل بشخصيات صعبة (كل محادثة تتكون من 20 رسالة) لتقييم أداء البوت في الحالات المعقدة.\n\n")
    
    scores = [d.get('evaluation', {}).get('score', 0) for d in data if 'evaluation' in d and 'score' in d['evaluation'] and isinstance(d['evaluation']['score'], int)]
    avg_score = sum(scores) / len(scores) if scores else 0
    f.write(f"## ملخص النتائج\n")
    f.write(f"**متوسط التقييم العام:** {avg_score:.2f} / 10\n\n")
    
    f.write("## التفاصيل لكل محادثة\n")
    for d in data:
        score = d.get("evaluation", {}).get("score", "N/A")
        notes = d.get("evaluation", {}).get("notes", "No notes")
        persona = d.get("persona", "")
        f.write(f"### محادثة {d['id']} (التقييم: {score}/10)\n")
        f.write(f"- **شخصية العميل:** {persona}\n")
        f.write(f"- **تعليق التقييم:** {notes}\n\n")
