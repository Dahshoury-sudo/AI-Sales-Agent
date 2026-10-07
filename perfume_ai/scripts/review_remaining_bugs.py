"""Reproduce adjacent bugs for review, without modifying AIAgent or contacting providers.

Run: python scripts/review_remaining_bugs.py
These checks describe current defects, rather than asserting fixed behavior.
"""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import dj_database_url
    from dotenv import dotenv_values
    os.environ['DJANGO_SETTINGS_MODULE'] = 'perfume_ai.settings_test'
    from django.conf import settings
    database = dj_database_url.parse(dotenv_values(ROOT / '.env')['TEST_DATABASE_URL'])
    assert database['HOST'] in ('localhost', '127.0.0.1', '::1')
    database['TEST'] = {'NAME': 'test_perfume_review_' + uuid.uuid4().hex[:10]}
    settings.DATABASES = {'default': database}
    import django
    django.setup()
    from django.db import transaction
    from django.test.runner import DiscoverRunner
    from django.contrib.admin.sites import AdminSite
    from products.admin import OrderAdmin
    from products.models import Store, StoreSettings, Brand, Product, ProductVariant, Conversation, Cart, Order, Message
    from products.services.conversation_service import save_message, receive_event, build_llm_history
    from products.services.order_service import handle_order, change_order_status
    from products.services.router import route
    from products.tasks import process_comment_task, process_incoming_message, _defer_or_drop, MAX_DEFERRALS

    def fixture():
        store = Store.objects.create(name='Review Store')
        StoreSettings.objects.create(store=store, messenger_access_token='test-token', facebook_page_id='test-page')
        brand = Brand.objects.create(store=store, name='Review Brand')
        product = Product.objects.create(store=store, brand=brand, name='Review Perfume', gender='male')
        ProductVariant.objects.create(product=product, volume=50, price=500)
        ProductVariant.objects.create(product=product, volume=90, price=800)
        original = ProductVariant.objects.create(product=product, volume=100, price=1000, bottle_type='original', stock=2)
        conv = Conversation.objects.create(store=store)
        def turn(products=None, confirmed=False):
            data = {'products': products or [], 'is_confirmed': confirmed,
                    'customer_name': 'Review', 'customer_phone': '01000000000',
                    'customer_secondary_phone': '01100000000', 'shipping_address': 'Synthetic test address'}
            with patch('products.services.order_service.chat', return_value=json.dumps(data)):
                reply, context = handle_order('review', [], store, conv)
            save_message(conv, 'assistant', reply, internal_context=context)
            return reply
        def line(volume, bottle_type='normal'):
            return {'name': product.name, 'quantity': 1, 'volume': volume, 'bottle_type': bottle_type}
        return store, conv, original, turn, line

    def same_product_sizes():
        store, conv, original, turn, line = fixture()
        turn([line(50), line(90)])
        before = list(conv.cart.items.order_by('variant__volume').values_list('variant__volume', 'quantity'))
        reply = turn([line(50), line(90)])
        after = list(Cart.objects.get(conversation=conv).items.order_by('variant__volume').values_list('variant__volume', 'quantity'))
        assert before == [(50, 1), (90, 1)] and after == [(50, 2), (90, 2)]
        return {'before': before, 'after': after, 'reply': reply, 'condition': 'Extractor returns the unchanged two-size cart without line_id fields'}

    def stale_admin_save():
        store, conv, original, turn, line = fixture()
        turn([line(100, 'original')])
        turn(confirmed=True)
        stale = Order.objects.get(conversation=conv)
        change_order_status(stale.pk, store, 'cancelled')
        original.refresh_from_db()
        after_cancel = original.stock
        stale.bot_notes = 'Staff note from an overlapping request'
        OrderAdmin(Order, AdminSite()).save_model(None, stale, SimpleNamespace(changed_data=['bot_notes']), True)
        reopened = Order.objects.get(pk=stale.pk).status
        change_order_status(stale.pk, store, 'cancelled')
        original.refresh_from_db()
        assert reopened == 'pending' and after_cancel == 2 and original.stock == 3
        return {'status_after_stale_save': reopened, 'stock_after_first_cancel': after_cancel, 'stock_after_second_cancel': original.stock}

    def cancelled_confirmation():
        store, conv, original, turn, line = fixture()
        turn([line(100, 'original')])
        turn(confirmed=True)
        order = Order.objects.get(conversation=conv)
        change_order_status(order.pk, store, 'cancelled')
        with patch('products.services.router.classify', return_value='order'):
            reply, _ = route('اكد الطلب', [], store, conv)
        order.refresh_from_db()
        assert 'تم تأكيد طلبك بنجاح' in reply and order.status == 'cancelled'
        return {'reply': reply, 'actual_order_status': order.status}

    def comment_replay_during_handoff():
        store, conv, original, turn, line = fixture()
        conv.platform = 'messenger'
        conv.platform_sender_id = 'review-sender'
        conv.needs_human = True
        conv.save()
        with patch('products.tasks.route', return_value=('comment reply', '')) as generate, patch('products.tasks.reply_to_comment', return_value={'id': 'public'}) as public, patch('products.tasks.send_private_reply', return_value=None) as private:
            for _ in range(2):
                process_comment_task.run(store.pk, 'facebook', 'same-comment-id', 'review-sender', 'Hello')
        assistant = list(conv.messages.filter(role='assistant').values('content', 'delivery_status'))
        assert generate.call_count == 2 and public.call_count == 2 and private.call_count == 2
        assert all(m['delivery_status'] == 'sent' for m in assistant)
        return {'human_handoff_active': True, 'replayed_comment_id': 'same-comment-id', 'generated_replies': generate.call_count,
                'public_send_attempts': public.call_count, 'private_send_attempts': private.call_count,
                'private_provider_result': None, 'saved_assistant_messages': assistant, 'model_history': build_llm_history(conv)}

    def formulation_intercepts_contacts():
        store, conv, original, turn, line = fixture()
        turn([line(50)])
        cart = Cart.objects.get(conversation=conv)
        cart.customer_phone = cart.secondary_phone = ''
        cart.quote = {}
        cart.save()
        with patch('products.services.router.handle_order') as checkout:
            reply, _ = route('موبايلي 01000000000 والبديل 01100000000. ومين المصنع؟', [], store, conv)
        cart.refresh_from_db()
        assert checkout.call_count == 0 and not cart.customer_phone and not cart.secondary_phone
        return {'checkout_calls': checkout.call_count, 'phone_saved': bool(cart.customer_phone), 'secondary_saved': bool(cart.secondary_phone), 'reply': reply}

    def dropped_event_revives():
        store, conv, original, turn, line = fixture()
        first = receive_event(store, 'messenger', 'review-sender', 'old request', 'first-event')
        _defer_or_drop(store.pk, 'messenger', 'review-sender', first.text, MAX_DEFERRALS, 10, 'sender', event_id=first.pk)
        first.refresh_from_db()
        after_drop = first.status
        second = receive_event(store, 'messenger', 'review-sender', 'new request', 'second-event')
        generated = []
        def answer(text, *args):
            generated.append(text)
            return text + ' reply', ''
        with patch('products.tasks.rate_limit.hit_all', return_value=(True, 0, None)), patch('products.tasks.route', side_effect=answer), patch('products.tasks.send_platform_message', return_value=True):
            process_incoming_message.run(store.pk, 'messenger', 'review-sender', second.text, event_id=second.pk)
        assert after_drop == 'pending' and generated == ['old request', 'new request']
        return {'status_after_max_deferrals': after_drop, 'generated_on_later_request': generated}

    cases = [('R01', same_product_sizes), ('R02', stale_admin_save), ('R03', cancelled_confirmation),
             ('R04', comment_replay_during_handoff), ('R05', formulation_intercepts_contacts), ('R06', dropped_event_revives)]
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old = runner.setup_databases()
    records = []
    try:
        with patch('requests.sessions.Session.request', side_effect=RuntimeError('External HTTP disabled')), patch('httpx.Client.send', side_effect=RuntimeError('External HTTP disabled')):
            for code, scenario in cases:
                try:
                    with transaction.atomic():
                        evidence = scenario()
                        transaction.set_rollback(True)
                    record = {'case': code, 'name': scenario.__name__, 'reproduced': True, 'evidence': evidence}
                except Exception as exc:
                    record = {'case': code, 'name': scenario.__name__, 'reproduced': False, 'error': repr(exc)}
                records.append(record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        runner.teardown_databases(old)
    destination = ROOT / 'artifacts' / 'bug_followup_review'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(r['reproduced'] for r in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
