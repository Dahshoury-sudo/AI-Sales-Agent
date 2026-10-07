"""Controlled failures for bugs that require timing, delivery or infrastructure.

All assistant replies are produced by application code except the two explicitly
labeled evaluator fixtures. No platform message is sent. Product-price/stock
experiments restore their changes while holding the affected row lock.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from decimal import Decimal
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from reproduce_commit_bugs import ROOT, setup, adapt_schema, state, persist_record


class Scenario:
    def __init__(self, code, title, mode):
        from products.models import Store, Conversation, ConversationEvaluation
        self.store = Store.objects.get(pk=1)
        self.conversation = Conversation.objects.create(store=self.store, platform='web', platform_sender_id=f'BUG-REPRO-{code}')
        self.record = {'code': code, 'title': title, 'mode': mode, 'conversation_id': self.conversation.pk,
                       'commit': '8ec2e69cfe26459135fe7dde5ef946fe4ddc1756', 'turns': [], 'events': [], 'status': 'running'}
        ConversationEvaluation.objects.create(conversation=self.conversation,
            evaluation_notes=f'BUG REPRO {code}: {title}. Controlled simulation: {mode}. Scores are not a quality evaluation.')
        persist_record(self.record)
        print(json.dumps({'started': code, 'conversation_id': self.conversation.pk, 'mode': mode}), flush=True)

    def turn(self, message, *, handler=None, extracted=None):
        from products.services.conversation_service import build_llm_history, save_message
        from products.services.router import route
        from products.services.reply_sanitizer import sanitize_reply
        history = build_llm_history(self.conversation)
        save_message(self.conversation, 'user', message)
        with ExitStack() as stack:
            if extracted is not None:
                stack.enter_context(patch('products.services.order_service.chat', return_value=json.dumps(extracted, ensure_ascii=False)))
            reply, context = (handler or route)(message, history, self.store, self.conversation)
            reply = sanitize_reply(reply, self.conversation)
        if reply:
            save_message(self.conversation, 'assistant', reply, internal_context=context)
        row = {'turn': len(self.record['turns'])+1, 'user': message, 'reply': reply,
               'context': context, 'state': state(self.conversation)}
        if extracted is not None:
            row['injected_extractor_output'] = extracted
        self.record['turns'].append(row)
        persist_record(self.record)
        print(json.dumps({'code': self.record['code'], 'reply': reply, 'state': row['state']}, ensure_ascii=False, default=str), flush=True)
        return reply

    def quote(self, *, product='Vanilo', volume=50, bottle='زجاجة البراند'):
        return self.turn(f'عايز أطلب واحدة {product} {volume} مل {bottle}. اسمي تجربة {self.record["code"]}، موبايلي 01000000000 والبديل 01100000000، عنواني القاهرة مدينة نصر 10 شارع الاختبار شقة 1.')

    def event(self, description, **evidence):
        self.record['events'].append({'description': description, **evidence})
        persist_record(self.record)

    def finish(self, reproduced, conclusion):
        from products.models import ConversationEvaluation, Order
        self.record['status'] = 'reproduced' if reproduced else 'not reproduced'
        self.record['conclusion'] = conclusion
        self.record['final_state'] = state(self.conversation)
        self.record['transcript'] = list(self.conversation.messages.order_by('id').values('id', 'role', 'content', 'internal_context'))
        Order.objects.filter(conversation=self.conversation).update(bot_notes=f'BUG REPRO {self.record["code"]} — synthetic simulation, DO NOT FULFILL.')
        ConversationEvaluation.objects.filter(conversation=self.conversation).update(evaluation_notes=(
            f'BUG REPRO {self.record["code"]}: {self.record["title"]}\n'
            f'Mode: {self.record["mode"]}\nResult: {self.record["status"]}\n{conclusion}\n'
            f'Evidence: {json.dumps(self.record["events"], ensure_ascii=False, default=str)}\n'
            'Synthetic diagnostic conversation; automatic quality scores are not meaningful.'))
        persist_record(self.record)
        print(json.dumps({'finished': self.record['code'], 'conversation_id': self.conversation.pk,
                          'status': self.record['status'], 'conclusion': conclusion}, ensure_ascii=False), flush=True)


def captured_cart(scenario, confirmed=True):
    from products.models import Cart
    cart = Cart.objects.get(conversation=scenario.conversation)
    return {'customer_name': cart.customer_name, 'customer_phone': cart.customer_phone,
            'customer_secondary_phone': cart.secondary_phone, 'shipping_address': cart.shipping_address,
            'products': [{'name': i.variant.product.name, 'volume': i.variant.volume,
                          'bottle_type': i.bottle_type, 'quantity': i.quantity}
                         for i in cart.items.select_related('variant__product')],
            'is_confirmed': confirmed, 'cart_cleared': False}


def stale_price():
    from django.db import transaction
    from products.models import ProductVariant, Order
    s = Scenario('C01', 'Price changed after quote is confirmed without a new summary', 'Live model; controlled catalog price change, restored before transaction commits')
    s.quote()
    with transaction.atomic():
        variant = ProductVariant.objects.select_for_update().get(product__name='Vanilo', product__store=s.store, volume=50, bottle_type='normal')
        before = variant.price
        try:
            variant.price = before + Decimal('100')
            variant.save(update_fields=['price'])
            s.event('Catalog price changed between quote and approval', old_price=str(before), new_price=str(variant.price))
            s.turn('تمام أكد الطلب.')
            totals = list(Order.objects.filter(conversation=s.conversation).values_list('total_price', flat=True))
        finally:
            variant.price = before
            variant.save(update_fields=['price'])
    s.event('Original catalog price restored', price=str(before))
    s.finish(before+100 in totals, f'The customer saw {before} EGP. Confirmation created order totals {totals} without first showing and approving the new price. Catalog price restored.')


def string_false():
    from products.models import Order
    from products.services.order_service import handle_order
    s = Scenario('C02', 'The string false is accepted as order confirmation', 'Live initial quote; malformed extractor boolean injected into the real checkout handler')
    s.quote()
    extracted = captured_cart(s, confirmed='false')
    s.event('Fault injection', field='is_confirmed', supplied_value='false', supplied_type='string', expected='Reject invalid extraction and preserve cart')
    s.turn('استنى، ما تأكدش الطلب لسه.', handler=handle_order, extracted=extracted)
    exists = Order.objects.filter(conversation=s.conversation).exists()
    s.finish(exists, 'The customer explicitly said not to confirm. Injecting JSON is_confirmed="false" still created an order because the string is truthy. This is an extractor-boundary test, not a claim that the live model emitted that JSON.')


def duplicate_confirmation():
    from django.db import connections, close_old_connections
    from products.models import Conversation, Order
    from products.services import order_service
    from products.services.conversation_service import save_message, build_llm_history
    s = Scenario('C03', 'Two concurrent confirmations create two orders for the same cart', 'Live initial quote; two synchronized valid checkout requests with recorded-cart extractor fixtures')
    s.quote()
    extracted = captured_cart(s)
    barrier = threading.Barrier(2)
    original = order_service.create_order_in_db
    def commit(*args, **kwargs):
        barrier.wait(timeout=20)
        return original(*args, **kwargs)
    def worker():
        close_old_connections()
        try:
            conv = Conversation.objects.get(pk=s.conversation.pk)
            history = build_llm_history(conv)
            save_message(conv, 'user', 'تمام أكد الطلب.')
            reply, context = order_service.handle_order('تمام أكد الطلب.', history, s.store, conv)
            save_message(conv, 'assistant', reply, internal_context=context)
            return reply
        finally:
            connections.close_all()
    with patch.object(order_service, 'chat', return_value=json.dumps(extracted)), patch.object(order_service, 'create_order_in_db', side_effect=commit), ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        replies = [f.result(timeout=40) for f in futures]
    ids = list(Order.objects.filter(conversation=s.conversation).values_list('id', flat=True))
    s.event('Both confirmations reached the commit boundary before either completed', order_ids=ids, replies=replies, injected_extractor_output=extracted)
    s.finish(len(ids)>1, f'Two simultaneous approvals of the same cart created {len(ids)} orders: {ids}.')


def notification_failure():
    from products.models import Order
    s = Scenario('C04', 'A notification failure reports order failure after the order has committed', 'Live checkout; dashboard-notification function raises a controlled exception')
    s.quote()
    with patch('products.services.order_service.notify_new_order', side_effect=RuntimeError('SIMULATED dashboard notification failure')):
        reply = s.turn('تمام أكد الطلب.')
    ids = list(Order.objects.filter(conversation=s.conversation).values_list('id', flat=True))
    s.event('Notification fault occurred after SQL order commit', order_ids=ids, reply=reply)
    s.finish(bool(ids) and 'مشكلة' in reply, f'Order(s) {ids} exist, but the customer received an order-failure message and was asked to retry.')


def repeated_cancellation():
    from django.db import transaction
    from products.models import ProductVariant, Order
    from products.views import OrderStatusUpdateView
    s = Scenario('C05', 'Reopening and cancelling a cancelled order restores stock twice', 'Live checkout/cancellation plus a simulated staff status change; stock restored under a row lock')
    with transaction.atomic():
        variant = ProductVariant.objects.select_for_update().get(product__name='Dior Sauvage', product__store=s.store, volume=100, bottle_type='original')
        initial = variant.stock
        try:
            s.quote(product='Dior Sauvage', volume=100, bottle='زجاجة أوريجينال')
            s.turn('تمام أكد الطلب.')
            order = Order.objects.filter(conversation=s.conversation).last()
            if not order:
                s.finish(False, 'The live setup did not create an order; cancellation race was not exercised.')
                return
            variant.refresh_from_db()
            sold = variant.stock
            s.turn('عايز ألغي الطلب ده بالكامل.')
            variant.refresh_from_db()
            cancelled_once = variant.stock
            response = OrderStatusUpdateView().patch(SimpleNamespace(store=s.store, data={'status': 'pending'}), order.pk)
            s.event('Simulated staff action: reopen cancelled order', response_status=response.status_code, response=response.data, stock=variant.stock)
            s.turn('الطلب ظهر معلق تاني، الغيه نهائي لو سمحت.')
            variant.refresh_from_db()
            cancelled_twice = variant.stock
            s.event('Inventory sequence', initial=initial, after_purchase=sold, after_first_cancellation=cancelled_once, after_second_cancellation=cancelled_twice)
        finally:
            variant.stock = initial
            variant.save(update_fields=['stock'])
    s.finish(cancelled_twice>initial, f'Stock changed {initial} → {sold} → {cancelled_once} → {cancelled_twice}. A cancelled order could be reopened without consuming stock, then cancelled to credit it again. Test restored stock to {initial}.')


def duplicate_web_request():
    from django.core.signing import Signer
    from products.views import ChatAPIView
    s = Scenario('D01', 'A repeated web request with the same client message ID is processed twice', 'Two identical requests to the real ChatAPIView.post handler; live model/static FAQ responses')
    payload = {'conversation_id': Signer().sign_object(s.conversation.pk), 'message': 'التوصيل بياخد وقت قد إيه؟', 'client_message_id': str(uuid.uuid4())}
    responses = [ChatAPIView().post(SimpleNamespace(store=s.store, data=payload)) for _ in range(2)]
    count = s.conversation.messages.filter(role='user', content=payload['message']).count()
    s.event('Identical request delivered twice', client_message_id=payload['client_message_id'], request=payload['message'], response_statuses=[r.status_code for r in responses], saved_user_copies=count, saved_assistant_copies=s.conversation.messages.filter(role='assistant').count())
    s.finish(count==2, 'One client_message_id produced two user messages and two assistant replies in the same conversation.')


def out_of_order():
    from django.core.signing import Signer
    from django.db import close_old_connections, connections
    from products import views
    s = Scenario('D02', 'Concurrent requests reply out of order and read incomplete history', 'Actual chat handler; a barrier delays the first request while the second completes')
    first = 'التوصيل بياخد وقت قد إيه؟'
    second = 'الدفع عند الاستلام موجود؟'
    entered, release = threading.Event(), threading.Event()
    original = views.route
    histories = {}
    def delayed(message, history, *args):
        histories[message] = history
        if message == first:
            entered.set()
            if not release.wait(timeout=30):
                raise RuntimeError('Diagnostic synchronization timed out')
        return original(message, history, *args)
    token = Signer().sign_object(s.conversation.pk)
    def send(message):
        close_old_connections()
        try:
            return views.ChatAPIView().post(SimpleNamespace(store=s.store, data={'message': message, 'conversation_id': token})).data
        finally:
            connections.close_all()
    with patch.object(views, 'route', side_effect=delayed), ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(send, first)
        try:
            if not entered.wait(timeout=10):
                raise RuntimeError('First request failed to enter router')
            second_response = executor.submit(send, second).result(timeout=25)
        finally:
            release.set()
        first_response = pending.result(timeout=30)
    messages = list(s.conversation.messages.order_by('id').values('role', 'content'))
    expected_missing = any(m['role']=='user' and m['content']==first for m in histories[second]) and not any(m['role']=='assistant' for m in histories[second])
    s.event('Processing order', messages=messages, second_request_history=histories[second], first_response=first_response, second_response=second_response)
    s.finish(expected_missing and messages[2]['content']==second_response.get('reply'), 'The saved order is customer 1, customer 2, reply 2, reply 1. The second request ran without the first reply in its history.')


def uncertain_delivery():
    from products import tasks
    s = Scenario('D03', 'A task retry resends a reply after delivery acknowledgement is lost', 'Simulated Messenger transport accepts a send then times out; no external messages are sent')
    s.conversation.platform = 'messenger'
    s.conversation.save(update_fields=['platform'])
    deliveries = []
    class ControlledRetry(Exception):
        pass
    def sender(conversation, body):
        deliveries.append(body)
        if len(deliveries)==1:
            raise TimeoutError('SIMULATED provider accepted message but acknowledgement was lost')
        return True
    with patch.object(tasks, 'send_platform_message', side_effect=sender), patch.object(tasks.rate_limit, 'hit_all', return_value=(True, 0, None)), patch.object(tasks.process_incoming_message, 'retry', side_effect=ControlledRetry):
        try:
            tasks.process_incoming_message.run(s.store.pk, 'messenger', s.conversation.platform_sender_id, 'التوصيل بياخد وقت قد إيه؟')
        except ControlledRetry:
            s.event('First delivery acknowledgement timed out; task requested a retry')
        tasks.process_incoming_message.run(s.store.pk, 'messenger', s.conversation.platform_sender_id, 'التوصيل بياخد وقت قد إيه؟')
    s.event('Simulated provider send attempts', count=len(deliveries), bodies=deliveries, real_external_sends=0)
    s.finish(len(deliveries)==2, 'The same worker input was saved and replied to twice after an ambiguous first delivery. The simulated provider received two sends; no actual customer was contacted.')


def web_handoff():
    from django.core.signing import Signer
    from django.urls import resolve, Resolver404
    from products.views import ChatAPIView, HandoffReplyAPIView
    s = Scenario('D04', 'Web chat cannot retrieve a staff reply during handoff', 'Real web chat and staff-reply handlers; synthetic staff text')
    s.turn('عايز أتكلم مع حد من خدمة العملاء لو سمحت.')
    staff_text = '[رد موظف تجريبي] أهلاً، أنا موجود أساعدك في اختيار العطر.'
    response = HandoffReplyAPIView().post(SimpleNamespace(store=s.store, data={'message': staff_text}), s.conversation.pk)
    customer_response = ChatAPIView().post(SimpleNamespace(store=s.store, data={'message': 'أنا لسه مستني، فين رد خدمة العملاء؟', 'conversation_id': Signer().sign_object(s.conversation.pk)}))
    try:
        resolve('/api/chat/messages/')
        endpoint_exists = True
    except Resolver404:
        endpoint_exists = False
    s.event('Staff reply is in the database but the public client has no retrieval route', staff_response_status=response.status_code, saved_staff_reply=staff_text, subsequent_chat_response=customer_response.data, public_polling_endpoint_exists=endpoint_exists)
    s.finish(not endpoint_exists and not customer_response.data.get('reply'), 'The staff reply was saved, but the next public POST returned an empty reply and /api/chat/messages/ does not exist in this checkout.')


def evaluator_wrong_price():
    from eval_harness.checks import build_ground_truth, check_reply
    from products.services.conversation_service import save_message
    s = Scenario('E01', 'Evaluator accepts another products genuine price as the requested products price', 'Explicitly injected incorrect assistant quote to exercise the real evaluator; not a live model response')
    question = 'Vanilo 50 مل زجاجة البراند بكام؟'
    wrong_reply = 'Vanilo — الـ50 مل بـ645 جنيه.'
    save_message(s.conversation, 'user', question)
    save_message(s.conversation, 'assistant', wrong_reply, internal_context='DIAGNOSTIC FIXTURE: deliberately wrong quote. Vanilo 50ml = 607 EGP; 645 EGP belongs to Bleu de Chanel 50ml. This reply tests the evaluator and was not produced by a live model.')
    findings = check_reply(wrong_reply, truth=build_ground_truth(s.store), context='', customer_text=question, turn_state={})
    s.event('Evaluate a known incorrect quote', correct_price='607.00', supplied_price='645.00', source_of_wrong_price='Bleu de Chanel 50ml', findings=findings)
    price_findings = [f for f in findings if any(word in str(f[0]) for word in ('price','quote','number'))]
    s.finish(not price_findings, f'The evaluator returned {findings}; it failed to flag 645 EGP for Vanilo 50ml, whose actual price is 607 EGP, because 645 is a valid price elsewhere in the catalog.')


CASES = {'C01': stale_price, 'C02': string_false, 'C03': duplicate_confirmation,
         'C04': notification_failure, 'C05': repeated_cancellation,
         'D01': duplicate_web_request, 'D02': out_of_order, 'D03': uncertain_delivery,
         'D04': web_handoff, 'E01': evaluator_wrong_price}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codes', nargs='*')
    args = parser.parse_args()
    setup()
    adapt_schema()
    from products.services.ai import client as ai_client
    ai_client.client = ai_client.client.with_options(timeout=90, max_retries=1)
    with ExitStack() as stack:
        stack.enter_context(patch('requests.sessions.Session.request', side_effect=RuntimeError('Diagnostic runner: external platform HTTP disabled')))
        stack.enter_context(patch('products.services.order_service.notify_new_order'))
        stack.enter_context(patch('products.services.router.notify_handoff'))
        stack.enter_context(patch('products.services.router.record_llm_message'))
        for code, fn in CASES.items():
            if args.codes and code not in args.codes:
                continue
            if (ROOT / 'artifacts' / 'bug_reproductions' / (code+'.json')).exists():
                print(json.dumps({'skip_existing': code}), flush=True)
                continue
            try:
                fn()
            except Exception as exc:
                print(json.dumps({'failed': code, 'error': f'{type(exc).__name__}: {exc}'}), flush=True)
                import traceback
                traceback.print_exc()


if __name__ == '__main__':
    main()
