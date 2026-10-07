"""Review saved diagnostic records and verify their persisted database evidence."""
import json
import subprocess
from reproduce_commit_bugs import ROOT, setup, adapt_schema, state, persist_record


REVIEWS = {
    'L01': (True, 2, 'Requested 2 Leatherio and 3 Vanilo. The first cart stored only Leatherio as pending, with no quantities. After choosing Leatherio 90ml, the reply showed just 1 Leatherio and omitted Vanilo. Turn 3 recovered the original quantities from still-visible history; the earlier loss remains visible.'),
    'L02': (True, 1, 'Mystery Azure was requested alongside Vanilo. Only Vanilo was saved and recapped; the unresolved name never became a pending item, even after the customer explicitly asked to retain it.'),
    'L03': (True, 2, 'The only quote showed 1 Vanilo at 607 EGP. The customer said to confirm but change the quantity to 2. The next reply confirmed order 66 at 1214 EGP without showing a revised summary for approval.'),
    'L04': (True, 2, 'The customer explicitly limited BOTH 90ml bottles to a combined 1000 EGP. The bot offered bottles at 700 and 780 EGP, then built a 1480 EGP cart and falsely said the 1000 EGP limit had been for one perfume.'),
    'L05': (True, 1, 'The customer said 90ml, at most 900 EGP, and explicitly refused even one extra pound. The bot still offered Dior Sauvage 90ml at 944 EGP. It disclosed the overrun but had no permission to offer an upgrade.'),
    'L06': (True, 2, 'The bot made unsupported relative-sweetness claims and contradicted itself in the same answer: it first called Black Opium less sweet than Bleu de Chanel, then described Bleu de Chanel as less sweet. This is a live model response, not an injected fixture.'),
    'L07': (True, 1, 'The customer explicitly named both Dior Sauvage and Bleu de Chanel and asked for price, longevity, projection and price per ml. The bot answered only Sauvage, asked the customer to type Bleu de Chanel again, and omitted the requested value comparison.'),
    'L08': (False, 2, 'The targeted authenticity confusion did not reproduce in this run. The store FAQ correctly said the perfumes are blends and that original-looking bottles can contain those blends. Kept as a control conversation, not counted as a reproduced bug.'),
    'L09': (True, 2, 'The customer withdrew the uncommon requirement: “مش لازم يكون نادر، عادي لو منتشر”. The persisted preferences still have wants_uncommon=true. The reply also called normal-bottle prices “original perfumes”; that secondary wording issue is not counted as a separate reproduction here.'),
    'L10': (True, 2, 'After switching from a personal winter purchase to a summer gift for a woman, explicitly dropping the prior conditions and budget, the database retained max_price=600 and projection=strong from the first recipient.'),
    'L11': (True, 6, 'The initial second recommendation was Acqua di Gio, which is active and stocked. After four unrelated questions, “the second one you recommended” produced an unavailable-product reply and an empty cart.'),
    'L12': (True, 2, 'When the customer supplied both phone numbers, a static FAQ returned the store contact numbers instead. Neither customer number was saved on that turn. The next follow-up recovered them from history; the intercepted checkout action is visible in turn 2.'),
    'L13': (False, 2, 'The targeted claim that an original bottle proves designer-made liquid was not explicitly made in this second probe. The model gave an evasive manufacturer answer and an over-budget alternative, but these do not establish the targeted authenticity claim. Retained as a control.'),
}

GROUPS = [
    ('Cart items disappearing or changing', ['L01', 'L02']),
    ('Premature or duplicate confirmation', ['L03', 'C01', 'C02', 'C03']),
    ('Stock/cancellation and committed-order notification errors', ['C05', 'C04']),
    ('Recommendations ignoring requirements', ['L04', 'L05', 'L06']),
    ('Incomplete or unsupported product answers', ['L07', 'L06', 'L08', 'L13']),
    ('Lost context, preferences, and FAQ misrouting', ['L09', 'L10', 'L11', 'L12']),
    ('Duplicate, out-of-order or uncertain message delivery', ['D01', 'D02', 'D03']),
    ('Missing staff replies in web chat', ['D04']),
    ('Evaluator missing a wrong variant price', ['E01']),
]


def main():
    setup()
    adapt_schema()
    from products.models import Conversation, ConversationEvaluation, ProductVariant, Order
    directory = ROOT / 'artifacts' / 'bug_reproductions'
    for code, (reproduced, focus, conclusion) in REVIEWS.items():
        path = directory / (code+'.json')
        record = json.loads(path.read_text(encoding='utf-8'))
        conv = Conversation.objects.get(pk=record['conversation_id'], store_id=1)
        record.update(status='reproduced' if reproduced else 'not reproduced', focus_turn=focus,
                      conclusion=conclusion, final_state=state(conv),
                      transcript=list(conv.messages.order_by('id').values('id', 'role', 'content', 'internal_context')))
        if any(t.get('error') for t in record['turns']):
            raise RuntimeError(f'{code} contains an unreviewed exception')
        ConversationEvaluation.objects.filter(conversation=conv).update(evaluation_notes=(
            f'BUG REPRO {code}: {record["title"]}\nMode: {record["mode"]}\n'
            f'Result: {record["status"]}; inspect turn {focus}.\n{conclusion}\n'
            'Synthetic diagnostic conversation. Automatic quality scores are not meaningful.'))
        persist_record(record)

    records = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(directory.glob('*.json'))
               if p.stem[:1] in {'L','C','D','E'} and p.stem[1:].isdigit()]
    by_code = {r['code']: r for r in records}
    assert len(records)==23, f'Expected 23 diagnostic conversations; got {len(records)}'
    assert all(r['status'] in {'reproduced','not reproduced'} for r in records)
    assert len({r['conversation_id'] for r in records})==len(records)
    verification = {'database': 'AIAgent', 'store_id': 1, 'store_name': 'Perfamix',
                    'checkout': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                    'conversations': []}
    for record in records:
        conv = Conversation.objects.get(pk=record['conversation_id'], store_id=1)
        assert conv.platform_sender_id==f'BUG-REPRO-{record["code"]}'
        saved = list(conv.messages.order_by('id').values('id','role','content','internal_context'))
        assert saved==record['transcript'], f'Saved transcript mismatch: {record["code"]}'
        for order in Order.objects.filter(conversation=conv):
            assert 'DO NOT FULFILL' in order.bot_notes, f'Unlabelled diagnostic order {order.pk}'
        verification['conversations'].append({'code': record['code'], 'id': conv.pk,
            'message_count': len(saved), 'order_ids': list(Order.objects.filter(conversation=conv).values_list('id',flat=True)), 'status': record['status']})
    baseline = json.loads((directory/'inspection.json').read_text(encoding='utf-8'))
    inventory_diff = []
    for product in baseline['catalog']:
        for original in product['variants']:
            current = ProductVariant.objects.get(pk=original['id'])
            if str(current.price)!=original['price'] or current.stock!=original['stock']:
                inventory_diff.append({'variant_id': current.pk, 'original_price': original['price'],
                    'current_price':str(current.price),'original_stock':original['stock'],'current_stock':current.stock})
    verification['catalog_price_or_stock_changes'] = inventory_diff
    assert not inventory_diff, 'Catalog price/stock differs from the pre-run snapshot'
    reproduced = [r for r in records if r['status']=='reproduced']
    verification['reproduced_count'] = len(reproduced)
    verification['control_count'] = len(records)-len(reproduced)
    (directory/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')

    lines = ['# Bug reproduction conversations', '',
        'Database: `AIAgent` on localhost. Store: **Perfamix (ID 1)**.',
        f'Checkout: `{verification["checkout"]}`. Date: 2026-10-06.', '',
        f'**{len(reproduced)} reproduced cases; {len(records)-len(reproduced)} control conversations where the targeted claim did not reproduce.**', '',
        'All conversations remain in the database. Their `platform_sender_id` starts with `BUG-REPRO-`; evaluation notes identify the setup, result and evidence. Test orders are labelled `DO NOT FULFILL`.', '',
        'L cases used the configured live models and real router without canned model replies. C/D cases exercised the real application handlers with the specific fault or timing condition stated below. E01 is a deliberately wrong reply supplied to the real evaluator, not a live model hallucination.', '',
        'The database still has migrations through 0041. The runner supplied neutral defaults for newer columns only in its own Python model metadata, leaving the application code and database schema as found. Newer database constraints therefore remain active. Existing catalog prices and stock were verified against the pre-run snapshot and match.', '',
        '## Coverage of the nine problem areas', '', '| Problem area | Conversations |', '|---|---|']
    for title, codes in GROUPS:
        refs = ', '.join(f'{by_code[c]["conversation_id"]} ({c}{", control" if by_code[c]["status"]!="reproduced" else ""})' for c in codes)
        lines.append(f'| {title} | {refs} |')
    lines += ['', 'The catalog has 40 active products, so the former 60-product cutoff was not exercised with synthetic catalog rows. Test-environment database/network isolation is infrastructure behavior and was not represented as a customer conversation. The simple last-bottle oversell claim is not asserted here; this checkout already rechecks original-bottle stock under a row lock. C05 demonstrates the separate stock-restoration defect.', '',
        '## Saved conversations', '', '| ID | Case | Result | Problem |', '|---:|---|---|---|']
    for r in sorted(records,key=lambda r:r['conversation_id']):
        lines.append(f'| {r["conversation_id"]} | {r["code"]} | {r["status"]} | {r["title"]} |')
    for r in sorted(records,key=lambda r:r['conversation_id']):
        lines += ['', f'## Conversation {r["conversation_id"]} — {r["code"]}', '', r['title'], '',
                  f'**Result:** {r["status"]}. **Setup:** {r["mode"]}.', '', r['conclusion'], '']
        if r.get('focus_turn'):
            turn = r['turns'][r['focus_turn']-1]
            lines += [f'**Inspect turn {r["focus_turn"]}.**', '', '**Customer:**', '', '> '+turn['user'].replace('\n','\n> '), '', '**Actual reply:**', '', '> '+turn['reply'].replace('\n','\n> '), '']
        elif r.get('events'):
            lines += ['**Diagnostic events:**', '']
            for event in r['events']:
                lines.append('- '+event['description'])
            lines.append('')
        lines += [f'[Full transcript, state and diagnostic evidence]({r["code"]}.json)', '']
    lines += ['## Verification', '',
              f'- {len(records)} unique conversations and their complete saved transcripts verified in AIAgent.',
              '- All simulation orders have diagnostic labels.',
              '- No catalog price or stock differences remain.',
              '- External platform delivery in the retry test was simulated; no customer messages were sent.',
              '- The injected wrong-price evaluator fixture and two controls are explicitly identified.', '']
    (directory/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(verification,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
