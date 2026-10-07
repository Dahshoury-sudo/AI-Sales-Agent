"""Reproduce historical bugs in explicitly selected local AIAgent database.

This is a diagnostic runner, not an application fix. Credentials are supplied via
BUG_REPRO_DATABASE_URL; the runner refuses any other host/database.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from contextlib import ExitStack
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def setup():
    url = os.environ.get('BUG_REPRO_DATABASE_URL', '')
    parsed = urlsplit(url)
    if parsed.hostname not in {'localhost', '127.0.0.1', '::1'} or parsed.path != '/AIAgent':
        raise SystemExit('Set BUG_REPRO_DATABASE_URL to the authorized local AIAgent database.')
    os.environ['DATABASE_URL'] = url
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'perfume_ai.settings')
    import django
    django.setup()
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), inet_server_addr()::text')
        database, host = cursor.fetchone()
        if database != 'AIAgent' or connection.settings_dict['HOST'] not in {'localhost', '127.0.0.1', '::1'}:
            raise SystemExit('Database identity check failed.')
    return database


def adapt_schema():
    """Supply neutral values for columns left by the newer DB migrations.

    Only this runner's model metadata changes; no schema changes or new handlers.
    """
    from django.db import models
    from products.models import Conversation, Message, Cart, CartItem, Order, Product, StoreSettings
    fields = {
        Conversation: {'sales_state': models.JSONField(default=dict)},
        Message: {'delivery_status': models.CharField(max_length=12, default='delivered')},
        Cart: {'agent_version': models.PositiveSmallIntegerField(default=1),
               'pending_items': models.JSONField(default=list),
               'quote_digest': models.CharField(max_length=64, default=''),
               'revision': models.PositiveIntegerField(default=0)},
        CartItem: {'line_id': models.CharField(max_length=64, default='')},
        Order: {'notification_pending': models.BooleanField(default=False),
                'pricing_snapshot': models.JSONField(default=dict)},
        Product: {'sensory_profile': models.JSONField(default=dict),
                  'formulation': models.CharField(max_length=30, default='unknown')},
        StoreSettings: {'agent_features': models.JSONField(default=dict)},
    }
    for model, extra in fields.items():
        present = {field.name for field in model._meta.fields}
        for name, field in extra.items():
            if name not in present:
                model.add_to_class(name, field)


LIVE_SCENARIOS = [
    ('L01', 'Multiple unsized items lose quantity or the second item', [
        'عايز أطلب 2 من Leatherio و3 من Vanilo في زجاجة البراند، لسه هحدد الأحجام.',
        'خلي Leatherio 90 مل.',
        'وVanilo 50 مل. راجعلي كده الطلب كله والكميات.'
    ]),
    ('L02', 'An unresolved product disappears from the cart', [
        'عايز أطلب Vanilo 50 مل زجاجة البراند وواحد كمان اسمه Mystery Azure 50 مل. الاتنين في نفس الطلب.',
        'Vanilo تمام، والعطر التاني لسه محتاجين نتأكد من اسمه، خليه في الطلب لحد ما أتأكد.',
        'راجعلي كل الحاجات اللي طلبتها، بما فيها اللي لسه مش متأكدين منها.'
    ]),
    ('L03', 'Confirmation combined with a quantity correction', [
        'عايز أطلب زجاجة Vanilo 50 مل زجاجة البراند. اسمي تجربة L03، موبايلي 01000000000 والبديل 01100000000، العنوان القاهرة مدينة نصر 10 شارع الاختبار شقة 1.',
        'تمام أكد الطلب بس خلي الكمية اتنين بدل واحدة.'
    ]),
    ('L04', 'Total budget for two bottles treated as a per-bottle budget', [
        'أنا راجل وعايز عطرين، زجاجتين كل واحدة 90 مل زجاجة البراند، ومعايا 1000 جنيه إجمالي للاتنين مع بعض، مش لكل واحدة. رشحلي اتنين أقدر أشتريهم بالمبلغ ده من غير ما أزود.',
        'اختارلي الاتنين وقولي السعر النهائي للاتنين مع بعض، الحد الأقصى ألف جنيه.'
    ]),
    ('L05', 'Requested size and exact budget not enforced during recommendation', [
        'عايز عطر رجالي للشغل، زجاجة البراند 90 مل، ومعايا 900 جنيه بالظبط ومش هقدر أزود جنيه. رشحلي اختيارين بس بالحجم ده وسعر كل واحد.'
    ]),
    ('L06', 'Unsupported relative sweetness recommendation', [
        'عايز عطر شبه Baccarat Rouge 540 بس أقل حلاوة منه، دي أهم حاجة، في حدود 1200 جنيه و50 مل.',
        'أنهي واحد أقل حلاوة فعلاً؟ وقولي على أساس إيه حكمت إنه أقل حلاوة.'
    ]),
    ('L07', 'A question about two products and several dimensions is only partly answered', [
        'قارنلي Dior Sauvage وBleu de Chanel: سعر 50 مل من زجاجة البراند لكل واحد كام، وثبات وفوحان كل واحد إيه، وأنهي واحد أوفر في سعر الملي؟'
    ]),
    ('L08', 'Original bottle confused with verified designer formulation', [
        'Dior Sauvage عندكم في زجاجة أوريجينال؟',
        'أنا بسأل عن السائل نفسه: هل أصلي مصنع ديور ولا تركيب؟ هل الزجاجة الأوريجينال لوحدها دليل إن العطر أصلي؟'
    ]),
    ('L09', 'An explicit false preference is replaced by the old true value', [
        'عايز عطر رجالي مش منتشر ومختلف، في حدود 1000 جنيه.',
        'خلاص مش لازم يكون نادر، عادي لو منتشر، رشحلي حاجة معروفة من ديور أو شانيل.'
    ]),
    ('L10', 'Changing recipient retains the previous recipients preferences', [
        'عايز لنفسي عطر رجالي تقيل للشتا وفي حدود 600 جنيه.',
        'سيبك من طلبي أنا خالص. دلوقتي عايز هدية لصاحبتي للصيف، مش نفس الشروط ومش محدد ميزانية للهدية.'
    ]),
    ('L11', 'The second recommended product is forgotten after intervening FAQs', [
        'أنا راجل وعايز عطر صيفي في حدود 1000 جنيه. رشحلي اختيارين بس بالترتيب الأول والتاني.',
        'التوصيل بياخد وقت قد إيه؟',
        'الدفع عند الاستلام موجود؟',
        'هل عندكم محل أقدر أزوره؟',
        'بتوصلوا للمحافظات؟',
        'عايز أطلب التاني اللي رشحته في الأول، 50 مل زجاجة البراند، واحد منه.'
    ]),
    ('L12', 'An FAQ intercepts contact details needed by checkout', [
        'عايز أطلب Vanilo 50 مل زجاجة البراند، واحدة. اسمي تجربة L12 وعنواني القاهرة مدينة نصر 10 شارع الاختبار.',
        'رقم موبايلي 01000000000 والرقم البديل 01100000000. وبتوصلوا للمحافظات؟',
        'أنا كتبتلك الرقمين فوق، إيه اللي ناقص في الطلب؟'
    ]),
    ('L13', 'Excluding store-exclusive products makes ordinary blends sound like designer originals', [
        'عايز عطر رجالي من الماركات العالمية في حدود 1000 جنيه، بلاش عطور Perfamix الحصرية. رشحلي اختيارين بأسعارهم.',
        'قولي نوع السائل اللي هيوصلني بالأسعار دي ومين اللي مصنّعه؟'
    ]),
]


def state(conversation):
    from products.models import Cart, Order
    conversation.refresh_from_db()
    cart = Cart.objects.filter(conversation=conversation).first()
    return {
        'preferences': conversation.preferences, 'needs_human': conversation.needs_human,
        'cart': None if not cart else {
            'id': cart.pk, 'pending_product': cart.pending_product.name if cart.pending_product_id else None,
            'customer_name': cart.customer_name, 'phone': cart.customer_phone,
            'backup': cart.secondary_phone, 'address': cart.shipping_address,
            'items': list(cart.items.values('variant__product__name', 'variant__volume', 'quantity', 'bottle_type')),
        },
        'orders': list(Order.objects.filter(conversation=conversation).values('id', 'total_price', 'status')),
    }


def persist_record(record):
    destination = ROOT / 'artifacts' / 'bug_reproductions'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / (record['code'] + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def run_live(codes):
    from products.models import Store, Conversation, ConversationEvaluation, Order
    from products.services.conversation_service import build_llm_history, save_message
    from products.services import router
    from products.services.reply_sanitizer import sanitize_reply
    from products.services.ai import client as ai_client
    # Keep configured models; bound a hung external call without altering prompts.
    ai_client.client = ai_client.client.with_options(timeout=90, max_retries=1)
    store = Store.objects.get(pk=1)
    with ExitStack() as stack:
        stack.enter_context(patch('requests.sessions.Session.request', side_effect=RuntimeError('Diagnostic runner: external platform HTTP disabled')))
        stack.enter_context(patch('products.services.order_service.notify_new_order'))
        stack.enter_context(patch('products.services.router.notify_handoff'))
        stack.enter_context(patch('products.services.router.record_llm_message'))
        for code, title, turns in LIVE_SCENARIOS:
            if codes and code not in codes:
                continue
            path = ROOT / 'artifacts' / 'bug_reproductions' / (code + '.json')
            if path.exists():
                print(json.dumps({'skip_existing': code}), flush=True)
                continue
            conversation = Conversation.objects.create(store=store, platform='web', platform_sender_id=f'BUG-REPRO-{code}')
            record = {'code': code, 'title': title, 'conversation_id': conversation.pk,
                      'commit': '8ec2e69cfe26459135fe7dde5ef946fe4ddc1756', 'mode': 'live model, actual router',
                      'status': 'needs review', 'turns': []}
            ConversationEvaluation.objects.create(conversation=conversation,
                evaluation_notes=f'BUG REPRO {code}: {title}. Diagnostic conversation with synthetic customer details. Verdict pending; scores are not a quality evaluation.')
            persist_record(record)
            print(json.dumps({'started': code, 'conversation_id': conversation.pk, 'title': title}), flush=True)
            for index, message in enumerate(turns, 1):
                history = build_llm_history(conversation)
                save_message(conversation, 'user', message)
                started = time.monotonic()
                try:
                    reply, context = router.route(message, history, store, conversation)
                    reply = sanitize_reply(reply, conversation)
                    error = None
                except Exception as exc:
                    reply, context = '', ''
                    error = f'{type(exc).__name__}: {exc}'
                if reply:
                    save_message(conversation, 'assistant', reply, internal_context=context)
                row = {'turn': index, 'user': message, 'reply': reply, 'context': context,
                       'error': error, 'seconds': round(time.monotonic()-started, 1), 'state': state(conversation)}
                record['turns'].append(row)
                Order.objects.filter(conversation=conversation).update(bot_notes=f'BUG REPRO {code} — synthetic simulation, DO NOT FULFILL.')
                persist_record(record)
                print(json.dumps({'code': code, 'turn': index, 'reply': reply, 'error': error, 'seconds': row['seconds'], 'state': row['state']}, ensure_ascii=False, default=str), flush=True)
            print(json.dumps({'finished': code, 'conversation_id': conversation.pk}), flush=True)


def inspect():
    from django.db import connection
    from products.models import Store, Product
    output = {'stores': list(Store.objects.values('id', 'name', 'owner_id'))}
    output['catalog'] = []
    for product in Product.objects.filter(store_id=1, is_active=True).select_related('brand').prefetch_related('variants').order_by('id'):
        output['catalog'].append({
            'id': product.pk, 'name': product.name, 'brand': product.brand.name,
            'gender': product.gender, 'notes': [product.top_notes, product.middle_notes, product.base_notes],
            'longevity': product.longevity, 'projection': product.projection,
            'variants': list(product.variants.values('id', 'volume', 'price', 'bottle_type', 'stock')),
        })
    with connection.cursor() as cursor:
        cursor.execute("SELECT app, name FROM django_migrations WHERE app='products' ORDER BY name DESC LIMIT 8")
        output['migrations'] = cursor.fetchall()
        cursor.execute("SELECT table_name, column_name, column_default FROM information_schema.columns WHERE table_name IN ('products_conversation','products_message','products_cart','products_cartitem','products_order','products_orderitem') AND is_nullable='NO' ORDER BY table_name, ordinal_position")
        output['required_columns'] = cursor.fetchall()
    destination = ROOT / 'artifacts' / 'bug_reproductions'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'inspection.json').write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({'database': 'AIAgent', 'stores': output['stores'], 'products': len(output['catalog']), 'migrations': output['migrations'], 'catalog_sample': output['catalog'][:6], 'required_columns': output['required_columns']}, ensure_ascii=False, default=str))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['inspect', 'live'])
    parser.add_argument('--codes', nargs='*')
    args = parser.parse_args()
    setup()
    if args.mode == 'inspect':
        inspect()
    else:
        adapt_schema()
        run_live(set(args.codes or []))
