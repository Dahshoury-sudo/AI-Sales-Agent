"""Exercise billing and failure accounting without making API requests."""
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from openai.types.chat import ChatCompletion

from scripts.evaluate_agent_budgeted import BudgetStop, Meter, token_cost


def completion(*, content="{}", finish_reason="stop", prompt=1000, cached=200,
               written=300, output=400, reasoning=250, write_field="cache_write_tokens"):
    return ChatCompletion(
        id="test-completion", object="chat.completion", created=0, model="gpt-6-luna",
        choices=[{"index": 0, "message": {"role": "assistant", "content": content},
                  "finish_reason": finish_reason}],
        usage={"prompt_tokens": prompt, "completion_tokens": output,
               "total_tokens": prompt + output,
               "prompt_tokens_details": {"cached_tokens": cached, write_field: written},
               "completion_tokens_details": {"reasoning_tokens": reasoning}},
    )


class EvaluationBudgetTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="perfume_luna_budget_")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.meter = Meter(Decimal("0.10"), self.path)

    def request(self, original, **overrides):
        kwargs = {"model": "gpt-6-luna", "reasoning_effort": "none",
                  "messages": [{"role": "user", "content": "Return a JSON object."}],
                  "response_format": {"type": "json_object"}}
        kwargs.update(overrides)
        return self.meter.wrap(original)(**kwargs)

    def test_real_sdk_usage_prices_disjoint_cache_tokens_and_counts_reasoning_once(self):
        original = mock.Mock(return_value=completion())
        self.request(original, reasoning_effort="high")
        # 500 ordinary input, 200 cached reads, 300 writes, and 400 total output.
        self.assertEqual(self.meter.spent, Decimal("0.0002895"))
        self.assertEqual(self.meter.reserved, 0)
        saved = json.loads((self.path / "usage.json").read_text(encoding="utf-8"))
        call = saved["calls"][0]
        self.assertEqual(call["reasoning_effort"], "high")
        self.assertEqual(call["reasoning_tokens"], 250)
        self.assertEqual(call["completion_tokens"], 400)
        self.assertEqual(call["cache_write_tokens"], 300)
        self.assertEqual(call["cached_tokens"], 200)
        self.assertGreaterEqual(call["seconds"], 0)

    def test_older_chat_cache_creation_field_is_priced(self):
        self.request(mock.Mock(return_value=completion(write_field="cache_creation_tokens")))
        self.assertEqual(self.meter.spent, Decimal("0.0002895"))
        self.assertEqual(self.meter.calls[0]["cache_write_tokens"], 300)

    def test_high_reasoning_gets_output_headroom_and_standard_processing(self):
        for effort, maximum in (("none", 2048), ("high", 25000)):
            with self.subTest(effort=effort):
                original = mock.Mock(return_value=completion())
                self.request(original, reasoning_effort=effort)
                self.assertEqual(original.call_args.kwargs["max_completion_tokens"], maximum)
                self.assertEqual(original.call_args.kwargs["service_tier"], "default")

    def test_cold_cache_at_reserved_input_and_output_limits_is_fully_covered(self):
        def original(**kwargs):
            maximum_input = len(json.dumps(kwargs, ensure_ascii=False).encode("utf-8")) + 1024
            return completion(prompt=maximum_input, cached=0, written=maximum_input,
                              output=kwargs["max_completion_tokens"], reasoning=0)

        self.request(original)
        self.assertEqual(self.meter.spent, self.meter.calls[0]["reserved_usd"])
        self.assertEqual(self.meter.reserved, 0)

    def test_budget_exhaustion_prevents_the_request(self):
        self.meter = Meter(Decimal("0.0001"), self.path)
        original = mock.Mock()
        with self.assertRaises(BudgetStop):
            self.request(original, reasoning_effort="high")
        original.assert_not_called()
        self.assertEqual(self.meter.spent, 0)
        self.assertEqual(self.meter.reserved, 0)

    def test_unpriced_or_implicit_reasoning_requests_never_reach_the_api(self):
        for overrides in ({"model": "unknown-model"}, {"reasoning_effort": None},
                          {"reasoning_effort": "medium"}, {"service_tier": "priority"}):
            with self.subTest(overrides=overrides):
                original = mock.Mock()
                with self.assertRaises(BudgetStop):
                    self.request(original, **overrides)
                original.assert_not_called()

    def test_missing_usage_retains_the_entire_reservation(self):
        response = completion()
        response.usage = None
        with self.assertRaises(BudgetStop):
            self.request(mock.Mock(return_value=response))
        self.assertEqual(self.meter.spent, self.meter.calls[0]["reserved_usd"])
        self.assertEqual(self.meter.calls[0]["status"], "uncertain_charged_maximum")
        self.assertEqual(self.meter.reserved, 0)

    def test_transport_failure_retains_the_entire_reservation(self):
        with self.assertRaises(TimeoutError):
            self.request(mock.Mock(side_effect=TimeoutError("test timeout")))
        self.assertEqual(self.meter.spent, self.meter.calls[0]["reserved_usd"])
        self.assertEqual(self.meter.reserved, 0)

    def test_invalid_json_and_truncation_remain_paid_and_visible_as_failures(self):
        for content, finish, error in (("", "stop", "empty_output"),
                                       (None, "stop", "empty_output"),
                                       ("{", "stop", "invalid_json"),
                                       ("[]", "stop", "json_object_required"),
                                       ("{}", "length", "truncated_output")):
            with self.subTest(error=error, content=content):
                before = self.meter.spent
                with self.assertRaisesRegex(ValueError, error):
                    self.request(mock.Mock(return_value=completion(content=content, finish_reason=finish)))
                self.assertEqual(self.meter.spent - before, Decimal("0.0002895"))
                self.assertEqual(self.meter.calls[-1]["response_error"], error)
                self.assertEqual(self.meter.calls[-1]["status"], "complete")
                self.assertEqual(self.meter.reserved, 0)

    def test_plain_prose_does_not_require_json(self):
        response = completion(content="Here are two recommendations.")
        self.assertIs(self.request(mock.Mock(return_value=response), response_format=None), response)
        self.assertNotIn("response_error", self.meter.calls[0])

    def test_legacy_model_limits_and_prices_remain_available(self):
        for model, maximum, expected in (("gpt-4.1-mini", 2048, "0.00098"),
                                         ("gpt-5-mini", 8192, "0.001005")):
            with self.subTest(model=model):
                before = self.meter.spent
                original = mock.Mock(return_value=completion(written=0))
                self.request(original, model=model, reasoning_effort=None)
                self.assertEqual(original.call_args.kwargs["max_completion_tokens"], maximum)
                self.assertEqual(self.meter.spent - before, Decimal(expected))

    def test_long_context_rates_apply_to_the_whole_request(self):
        self.assertEqual(token_cost("gpt-6-luna", 300000, 100000, 1000, 50000), Decimal("0.04525"))
        self.assertEqual(token_cost("gpt-6-luna", 272000, 0, 1000, 272000), Decimal("0.0345"))
        self.assertEqual(token_cost("gpt-6-luna", 272001, 0, 1000, 272001), Decimal("0.06875025"))

    def test_invalid_cache_counts_cannot_understate_the_bill(self):
        for prompt, cached, output, written in ((100, 90, 10, 20), (100, -1, 10, 0),
                                                (100, 0, 10, -1), (100, 0, -10, 0)):
            with self.subTest(counts=(prompt, cached, output, written)):
                with self.assertRaises(BudgetStop):
                    token_cost("gpt-6-luna", prompt, cached, output, written)
