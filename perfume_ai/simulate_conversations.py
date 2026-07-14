import os
import sys
import django
import json
import concurrent.futures
from dotenv import load_dotenv

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'perfume_ai.settings')
django.setup()

from products.services.router import route
from products.models import Conversation, Store
import openai

load_dotenv()
openai.api_key = os.getenv('OPENAI_API_KEY')

MODEL = "gpt-4o-mini"

personas = [
    "عميل مصري غاضب جداً لأن الطلب السابق تأخر، ويريد الآن تعويض أو خصم كبير على عطر غالي. يتحدث بلهجة مصرية حادة.",
    "عميل مصري متردد جداً، يسأل عن عطر معين ثم يغير رأيه عدة مرات بين عطور مختلفة. يسأل عن تفاصيل دقيقة ومملة.",
    "عميل مصري يسأل عن أشياء خارج النطاق تماماً (مثل أسعار السيارات أو الطقس) ويحاول إجبار البوت على الحديث فيها.",
    "عميلة مصرية تبحث عن عطر رومانسي وهادئ جداً ولكن لا تحب الفانيليا أو الورد، وترفض معظم الاقتراحات.",
    "عميل مصري فاهم جداً في العطور ويسأل عن النوتات الافتتاحية والقلب والقاعدة بالتفصيل الممل لعطر خشبي.",
    "عميل مصري يريد شراء هدايا لـ 5 أشخاص مختلفين الأذواق ويطلب اقتراحات لكل واحد ثم يسأل عن الخصومات للكميات.",
    "عميل يطلب عطر، وبعد أن يتم تسجيل الطلب يطلب الإلغاء فوراً، ثم يطلب عطر آخر، ثم يلغيه.",
    "عميل مصري يتكلم لغة مزيج بين العربي والإنجليزي (فرانكو) بشكل مستمر ويسأل عن بدائل لعطور عالمية غير موجودة.",
    "عميل مصري يسأل عن عطر رخيص جداً لكن بثبات عالي جداً (أكثر من يومين) ولا يقتنع بسهولة.",
    "عميل مصري يقارن بين 4 عطور مختلفة ويسأل عن الفروق الدقيقة بينها، ثم يسأل أيهم يجلب إطراءات أكثر.",
    "عميل مصري يسأل عن مكونات غير منطقية في العطور (مثلا عطر برائحة البحر والمطاط!)",
    "عميل مصري يتهم البوت بأنه لا يفهم شيئاً ويطلب التحدث مع خدمة العملاء البشرية أكثر من مرة.",
    "عميلة مصرية تسأل عن عطور تناسب الأطفال الرضع وهل هي آمنة أم لا، وتطرح أسئلة طبية عن الحساسية.",
    "عميل مصري يريد فتح مشروع لبيع العطور ويسأل البوت عن نصائح وكيف يشتري بالجملة من المتجر.",
    "عميل مصري يرسل رسائل قصيرة جداً (كلمة واحدة في كل مرة) مثل 'عطر'، 'بكام'، 'غالي'، 'لا'.",
    "عميل مصري يطلب عطر ثم يسأل عن تفاصيل الشحن لكل محافظات مصر ومواعيد الاستلام بالتفصيل.",
    "عميل مصري كبير في السن، يكتب ببطء وبأخطاء إملائية كثيرة ويسأل عن عطور كلاسيكية قديمة.",
    "عميل مصري يسأل عن كيفية صنع العطور في المنزل ويطلب نسب الخلط والزيوت العطرية.",
    "عميلة مصرية تبحث عن عطر يشبه رائحة المطر والتراب المبلل وتسأل إذا كان متوفراً.",
    "عميل مصري ساخر جداً، يلقي نكات ويسخر من ردود البوت طوال الوقت ويختبر صبر البوت."
]

def simulate_user_turn(persona, history):
    system_prompt = f"""
أنت تلعب دور {persona}
أنت تتحدث مع بوت ذكاء اصطناعي لمتجر عطور.
ردودك يجب أن تكون قصيرة وطبيعية كأنك في شات (رسالة واحدة في كل مرة).
لا تخرج عن الشخصية أبداً.
إذا انتهيت من كل ما تريده يمكنك إنهاء المحادثة بكلمة مثل 'سلام'.
"""
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": "assistant" if msg["role"] == "assistant" else "user", "content": msg["content"]})
        
    response = openai.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.8,
        max_tokens=150
    )
    return response.choices[0].message.content.strip()

def evaluate_conversation(history):
    history_text = "\\n".join([f"{msg['role']}: {msg['content']}" for msg in history])
    system_prompt = """
قم بتقييم محادثة البوت التالية من 1 إلى 10 بناءً على:
1- فهم البوت لطلب المستخدم مهما كان صعباً أو معقداً.
2- احترافية البوت وعدم خروجه عن سياق متجر العطور (التهذيب، الصبر، المساعدة).
3- تقديم معلومات صحيحة أو توجيه صحيح للعميل.

أعطني النتيجة بصيغة JSON كالتالي:
{
  "score": 8,
  "notes": "شرح عن سبب التقييم"
}
"""
    response = openai.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": history_text}
        ],
        response_format={ "type": "json_object" },
        temperature=0.0
    )
    return json.loads(response.choices[0].message.content)

def run_single_conversation(idx, persona):
    print(f"Starting conversation {idx+1}...")
    from django.db import close_old_connections
    close_old_connections()
    
    store = Store.objects.first()
    if not store:
        store = Store.objects.create(name="متجر تجريبي", description="متجر تجريبي")
        
    conversation = Conversation.objects.create(store=store)
    
    history = []
    
    for turn in range(20): # 20 turns
        try:
            user_msg = simulate_user_turn(persona, history)
            history.append({"role": "user", "content": user_msg})
            
            bot_history = [{"role": m["role"], "content": m["content"]} for m in history[:-1]]
            bot_reply, _ = route(user_msg, history=bot_history, store=store, conversation=conversation)
            
            history.append({"role": "assistant", "content": bot_reply})
            
            import time
            time.sleep(2) # Avoid rate limit
            
            if turn > 15 and "سلام" in user_msg.lower():
                break
        except Exception as e:
            print(f"Error in conversation {idx+1} turn {turn+1}: {str(e)}")
            history.append({"role": "system_error", "content": str(e)})
            break
            
    print(f"Conversation {idx+1} finished. Evaluating...")
    evaluation = {}
    try:
        evaluation = evaluate_conversation(history)
    except Exception as e:
        evaluation = {"error": str(e)}
        
    return {
        "id": idx + 1,
        "persona": persona,
        "history": history,
        "evaluation": evaluation
    }

def main():
    results = []
    print("Starting bot stress test with 20 conversations...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future_to_idx = {executor.submit(run_single_conversation, i, personas[i]): i for i in range(20)}
        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result = future.result()
                results.append(result)
                print(f"Conversation {idx+1} processed successfully. Score: {result.get('evaluation', {}).get('score', 'N/A')}")
            except Exception as exc:
                print(f"Conversation {idx+1} generated an exception: {exc}")

    results.sort(key=lambda x: x["id"])
    
    with open("test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        
    print("Test completed. Results saved to test_results.json")

if __name__ == "__main__":
    main()
