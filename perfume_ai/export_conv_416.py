import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'perfume_ai.settings')
django.setup()

from products.models import Conversation, ConversationEvaluation

try:
    conv = Conversation.objects.get(id=416)
    with open('conv_416.md', 'w', encoding='utf-8') as f:
        f.write(f"# Conversation 416\nNeeds Human: {conv.needs_human}\n\n## Messages\n")
        for m in conv.messages.all():
            f.write(f"**{m.role}**: {m.content}\n\n")
            if m.internal_context:
                f.write(f"*(Context: {m.internal_context})*\n\n")
        
        f.write("## Evaluation\n")
        eval = ConversationEvaluation.objects.filter(conversation=conv).first()
        if eval:
            f.write(f"Overall Score: {eval.overall_score}\n")
            f.write(f"Notes:\n{eval.evaluation_notes}\n")
        else:
            f.write("No evaluation found.\n")
except Exception as e:
    with open('conv_416.md', 'w', encoding='utf-8') as f:
        f.write(f"Error: {e}")
