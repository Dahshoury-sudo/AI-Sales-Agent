import json
import os

input_file = "test_results.json"
output_file = r"C:\Users\Mohamed\.gemini\antigravity-ide\brain\838b80d0-85b6-4a5c-80ca-175cf22ec7bb\conversation_logs.md"

with open(input_file, "r", encoding="utf-8") as f:
    data = json.load(f)

with open(output_file, "w", encoding="utf-8") as out:
    out.write("# سجل المحادثات للاختبار الأخير\n\n")
    out.write("هنا تقدر تراجع المحادثات بنفسك وتشوف البوت اتصرف إزاي في المواقف المختلفة.\n\n")
    
    # Pick specific interesting conversations, or all of them. Let's do 1, 3, 7, 12, 18, 20
    interesting_ids = [1, 3, 7, 12, 18, 20]
    
    for d in data:
        if d["id"] in interesting_ids:
            out.write(f"## محادثة رقم {d['id']} | التقييم: {d['evaluation'].get('score')}/10\n")
            out.write(f"**الشخصية:** {d['persona']}\n\n")
            
            for i, msg in enumerate(d["history"]):
                role = "🧑 العميل الوهمي" if msg["role"] == "user" else "🤖 البوت"
                out.write(f"**{role}:** {msg['content']}\n\n")
                
            out.write("---\n\n")
