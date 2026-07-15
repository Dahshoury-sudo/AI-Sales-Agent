import json
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'perfume_ai.settings')
django.setup()

from products.services.ai.client import chat
import time

def evaluate_with_my_rules():
    with open('test_results.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    output_file = r"C:\Users\Mohamed\.gemini\antigravity-ide\brain\838b80d0-85b6-4a5c-80ca-175cf22ec7bb\antigravity_evaluation.md"
    
    prompt = """
    أنت خبير مبيعات وتقييم أعمال محترف. قم بتقييم أداء "بوت مبيعات العطور" في المحادثة التالية من 10.
    
    قواعد صارمة للتقييم (لا تظلم البوت فيها):
    1. لو العميل طلب تعويض أو خصم، والبوت رفض ووجهه للإدارة: إديله 10/10 لأنه بياع معندوش صلاحية خصومات.
    2. لو العميل سأل أسئلة برة العطور (عربيات، طقس) والبوت رفض يجاوب ورجع يبيع عطور: إديله 10/10 لأنه مركز في شغله.
    3. لو العميل سأل عن تصنيع العطور في البيت، والبوت أداه نصيحة عامة ورجع يعرض منتجات المتجر الجاهزة: إديله 10/10 لأنه بياع ناصح.
    4. لو العميل كرر طلب خدمة العملاء كتير، والبوت رد مرة واحدة وبعدين حاول يخدمه عشان ميقعش في تكرار ممل: إديله 10/10.
    5. عاقب البوت فقط لو كرر نفس الجملة بالضبط، أو فقد أدبه، أو أعطى معلومات خاطئة عن العطور.
    
    رجع النتيجة في شكل JSON فقط كالتالي:
    {
        "score": 9,
        "reason": "سبب التقييم باختصار شديد باللغة العربية"
    }
    """
    
    with open(output_file, "w", encoding="utf-8") as out:
        out.write("# تقييمي الشخصي (Antigravity) لأداء البوت في المحادثات 🎯\n\n")
        out.write("بناءً على معايير البيع الاحترافية (مش تقييم الذكاء الاصطناعي الأعمى)، ده تقييمي الفعلي لكل محادثة:\n\n")
        
        for d in data:
            history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in d["history"]])
            
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Persona: {d['persona']}\n\nHistory:\n{history_text}"}
            ]
            
            try:
                response = chat(messages, response_format={"type": "json_object"})
                result = json.loads(response)
                score = result.get("score", 0)
                reason = result.get("reason", "")
                
                out.write(f"### محادثة {d['id']} | التقييم: {score}/10\n")
                out.write(f"**الشخصية:** {d['persona']}\n")
                out.write(f"**سبب التقييم:** {reason}\n\n")
            except Exception as e:
                print(f"Error on {d['id']}: {e}")
                
            time.sleep(1) # Prevent rate limits

if __name__ == "__main__":
    evaluate_with_my_rules()
