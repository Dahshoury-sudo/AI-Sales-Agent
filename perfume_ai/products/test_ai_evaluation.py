"""The replay probes must preserve the arguments used by the current router."""
from contextlib import ExitStack
from unittest.mock import patch

from django.test import SimpleTestCase


class EvaluationProbeTests(SimpleTestCase):
    def test_promotion_and_preference_context_reaches_the_wrapped_services(self):
        from eval_harness import runner
        from products.services import router
        from products.services.sales import objection, stage

        targets = [(router, name) for name in (
            "classify", "extract_intent", "merge_preferences", "search_products",
            "recommend", "_finalize",
        )] + [(stage, "derive"), (objection, "detect")]
        with ExitStack() as stack:
            originals = {name: stack.enter_context(patch.object(module, name))
                         for module, name in targets}
            originals["classify"].return_value = "promotion"
            originals["merge_preferences"].return_value = {"gender": "male"}
            runner.install_probes()
            history = [{"role": "user", "content": "Earlier request"}]
            conversation, store = object(), object()
            self.assertEqual(router.classify("Hello", history, promotion_context="Offer facts"), "promotion")
            originals["classify"].assert_called_once_with("Hello", history, promotion_context="Offer facts")
            result = router.merge_preferences(conversation, {}, "Hello", pending={"gender": True},
                                              history=history, store=store)
            originals["merge_preferences"].assert_called_once_with(
                conversation, {}, "Hello", pending={"gender": True}, history=history, store=store,
            )
            self.assertEqual(result, {"gender": "male"})
            self.assertEqual(runner._rec()["classification"], "promotion")
            self.assertEqual(runner._rec()["merged_intent"], result)
