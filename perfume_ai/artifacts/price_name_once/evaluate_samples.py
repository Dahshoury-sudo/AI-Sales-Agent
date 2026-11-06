"""Run focused price conversations in the existing disposable local evaluator."""
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parents[2]
output = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / 'artifacts/price_name_once/live'
selected = set(sys.argv[2].split(',')) if len(sys.argv) > 2 else None
sys.path.insert(0, str(root))
from eval_harness.scenarios import SCENARIOS

cases = [
    ('NAME_PRICE', ['سوفاج بكام؟']),
    ('NAME_NOTES', ['سوفاج بكام وإيه نوتاته؟']),
    ('NAME_PERFORMANCE', ['سوفاج بكام وثباته قد إيه؟']),
    ('NAME_MULTI', ['سوفاج وبلو دي شانيل بكام؟']),
    ('NAME_FOLLOWUP', ['سوفاج بكام؟', 'طيب الـ50 ملي بكام؟']),
]
SCENARIOS[:] = [
    {'id': key, 'category': 'purchase', 'persona': 'ready_buyer', 'turns': turns,
     'probe': 'Show each quoted perfume name exactly once as a heading above its requested details and price options. Preserve all requested facts, sizes, bottle types, and exact prices; no repeated name in an opening or variant line.'}
    for key, turns in cases if selected is None or key in selected
]
sys.argv = [str(root / 'scripts/evaluate_agent_budgeted.py'), '--budget', '0.20',
            '--suite', 'core', '--workers', '3', '--output',
            str(output)]
runpy.run_path(sys.argv[0], run_name='__main__')
