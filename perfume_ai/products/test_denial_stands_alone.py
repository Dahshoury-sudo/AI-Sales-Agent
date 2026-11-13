"""A perfume we do not carry is denied, and the reply stops there.

Store owner, 2026-11-13: "when someone asks for a perfume that is unavailable i want you to say
it's not available and stop. don't offer alternatives."

This reverses the rule the absence work shipped with — a denial used to *require* one or two stocked
perfumes in the same reply. The tests below hold the new rule at three levels: no substitute row
reaches the model, the prompt forbids a pitch, and a pitch the model writes anyway is trimmed off
before the customer sees it.
"""
from unittest import mock

from django.test import TestCase

from products.models import Brand, Conversation, Notification, Product, ProductVariant, Store
from products.services.conversation_service import build_llm_history, save_message
from products.services.product_info import get_product_info
from products.services.product_resolver import Resolution
from products.services.router import route

ROW = "Name (الاسم الصحيح)"
SHELF = "بدائل مقترحة متوفرة في المتجر"

_confirm_pass_finds_nothing = mock.patch(
    "products.services.product_info.confirm_unplaced",
    lambda name, store=None: None,
)


def _absent(*names):
    return Resolution([], names)


class _Store(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.sport = self._perfume("Dior", "Dior Homme Sport", 450, "Woody Notes, Amber, Olibanum")
        self.bleu = self._perfume("Chanel", "Bleu de Chanel", 645, "Incense, Cedar, Sandalwood")
        self.conversation = Conversation.objects.create(store=self.store, platform="whatsapp")

    def _perfume(self, brand, name, price, base=""):
        brand, _ = Brand.objects.get_or_create(store=self.store, name=brand)
        product = Product.objects.create(
            store=self.store, brand=brand, name=name, gender="male", base_notes=base)
        ProductVariant.objects.create(product=product, volume=50, price=price, bottle_type="normal")
        return product

    def _offered(self, *products):
        for product in products:
            save_message(
                self.conversation, "assistant", f"ممكن أنصحك بـ {product.name}.",
                internal_context=f"{ROW}: {product.name}",
            )

    def _info(self, message, resolved, reply="ok"):
        with mock.patch(
            "products.services.product_info.resolve_products", return_value=resolved
        ), mock.patch(
            "products.services.product_info.chat", return_value=reply
        ) as chat, mock.patch(
            "products.services.product_info.suggest_alternatives", create=True
        ) as suggest:
            response, context = get_product_info(message, [], self.store, self.conversation)
        prompt = chat.call_args[0][0][-1]["content"] if chat.called else ""
        return response, context, prompt, suggest

    def _route(self, message, resolved, reply):
        history = build_llm_history(self.conversation)
        save_message(self.conversation, "user", message)
        with mock.patch(
            "products.services.router.classify", return_value="product_info"
        ), mock.patch(
            "products.services.product_info.resolve_products", return_value=resolved
        ), mock.patch(
            "products.services.product_info.chat", return_value=reply
        ) as chat, mock.patch("products.services.router.notify_handoff"):
            response, context = route(message, history, self.store, self.conversation)
        save_message(self.conversation, "assistant", response, internal_context=context)
        self.conversation.refresh_from_db()
        return response, context, chat


@_confirm_pass_finds_nothing
class DeniedTurnCarriesNothingToPitchTests(_Store):
    """What the model is handed on the turn that denies."""

    def test_a_first_ask_carries_no_perfume(self):
        from products.services import product_info

        _, context, prompt, suggest = self._info("عندك لادور بخور ؟", _absent("لادور بخور"))

        self.assertIn(product_info.ABSENCE_DENIED_MARKER, context)
        self.assertNotIn(ROW, context)
        self.assertNotIn(SHELF, context)
        self.assertNotIn("Dior Homme Sport", prompt.split("═══ تعليمات")[0])
        suggest.assert_not_called()

    def test_perfumes_already_on_the_table_are_not_brought_along(self):
        from products.services import product_info

        self._offered(self.bleu, self.sport)

        _, context, _, _ = self._info("عندك لادور بخور ؟", _absent("لادور بخور"))

        self.assertIn(product_info.ABSENCE_DENIED_MARKER, context)
        self.assertNotIn("Dior Homme Sport", context)
        self.assertNotIn("Bleu de Chanel", context)
        self.assertNotIn(ROW, context)

    def test_the_prompt_asks_for_the_denial_and_nothing_else(self):
        from products.services import product_info

        _, _, prompt, _ = self._info("عندك لادور بخور ؟", _absent("لادور بخور"))

        self.assertIn(product_info._ABSENT_RULES, prompt)
        self.assertIn(product_info._DENIAL_ONLY_RULE, prompt)
        self.assertNotIn("اعرض عليه بديل", prompt)
        self.assertNotIn("ممنوع ترد بنفي لوحده", prompt)
        self.assertNotIn("البدائل اللي بتعرضها", prompt)
        self.assertIn("ممنوع تسمّي أو ترشح أو تسعّر أي عطر تاني", prompt)
        # "confirm availability" is the opposite instruction and must not ride along.
        self.assertNotIn("العميل سأل عن التوفر بس", prompt)
        self.assertEqual(prompt.count("\n14."), 1)
        for number in range(1, 6):
            self.assertIn(f"\n{number}. ", prompt)

    def test_a_chase_is_denied_again_with_nothing_on_offer(self):
        from products.services import product_info

        self._route("عندك لادور بخور ؟", _absent("لادور بخور"), "للأسف لادور بخور مش موجود عندنا.")

        _, context, prompt, _ = self._info("اتأكدلي منه", [])

        self.assertIn(product_info.ABSENCE_DENIED_MARKER, context)
        self.assertNotIn(ROW, context)
        self.assertIn(product_info._ABSENT_RULES, prompt)

    def test_a_bare_price_question_after_the_denial_restates_it(self):
        """Nothing else is on the table, so "بكام ده؟" can only be about the perfume we just denied."""
        from products.services import product_info

        self._route("عندك لادور بخور ؟", _absent("لادور بخور"), "للأسف لادور بخور مش موجود عندنا.")

        _, context, _, suggest = self._info("بكام ده؟", [])

        self.assertIn(product_info.ABSENCE_DENIED_MARKER, context)
        self.assertNotIn(SHELF, context)
        suggest.assert_not_called()

    def test_the_table_from_before_the_denial_survives_it(self):
        """A denial names nothing, so what we had offered is still what "دول" points at."""
        from products.services import product_info

        self._offered(self.bleu)
        self._offered(self.sport)
        self._route("عندك لادور بخور ؟", _absent("لادور بخور"), "للأسف لادور بخور مش موجود عندنا.")

        _, context, _, _ = self._info("طب عاملين كام دو", [])

        self.assertNotIn(product_info.ABSENCE_DENIED_MARKER, context)
        self.assertIn("Dior Homme Sport", context)

    def test_a_partial_denial_keeps_the_perfumes_that_were_asked_about(self):
        from products.services import product_info

        resolved = Resolution([self.bleu], ("الكساندريا 2",))

        _, context, prompt, _ = self._info(
            "عايز اعرف اسعار بلو دي شانيل والكساندريا 2", resolved)

        self.assertIn(product_info.ABSENCE_DENIED_MARKER, context)
        self.assertIn("Bleu de Chanel", context)
        self.assertIn(product_info._PARTIAL_ABSENT_RULES, prompt)
        self.assertFalse(product_info.denial_only(context))

    def test_a_question_that_names_nothing_gets_no_shelf_either(self):
        _, context, _, suggest = self._info("عندكم فرع؟", [])

        self.assertNotIn(SHELF, context)
        suggest.assert_not_called()

    def test_the_unreadable_rule_no_longer_promises_alternatives_after_a_denial(self):
        from products.services import product_info

        self.assertNotIn("البدائل مكانها", product_info._UNREADABLE_NAME_RULES)
        self.assertIn("وممنوع تعرض عليه أي عطر تاني في الرد ده", product_info._UNREADABLE_NAME_RULES)


class DenialAloneTests(TestCase):
    """`described.denial_alone`: the clause-level trim behind the router's last line of defence."""

    NAMES = ["Dior Homme Sport", "Bleu de Chanel", "1 Million", "Y"]

    def _trim(self, reply, asked="عندك لادور بخور ؟"):
        from products.services.sales import described

        return described.denial_alone(reply, self.NAMES, asked)

    def test_a_clean_denial_is_left_exactly_as_written(self):
        for reply in (
            "للأسف لادور بخور مش موجود عندنا يا فندم.",
            "بعتذر يا فندم، لادور بخور مش متوفر عندنا.",
            "زي ما قلتلك يا فندم، العطر ده مش موجود عندنا.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(self._trim(reply), reply)

    def test_a_pitch_is_cut_and_the_denial_kept(self):
        denial = "للأسف لادور بخور مش موجود عندنا"
        for tail in (
            ". بس عندنا Dior Homme Sport.",
            "، بس عندنا Dior Homme Sport وBleu de Chanel.",
            ". ممكن تشوف 1 Million.",
            ". كبدائل متوفرة:\n1 Million: طابعه مسكر.",
            ". تحب أرشحلك حاجة قريبة منه؟",
            "، لكن ممكن أرشحلك بديل.",
            ". بس عندنا عطور تانية حلوة.",
            ". أقدر ألاقيلك حاجة شبهه لو تحب.",
            ". تحب أعرفلك عن حجم معين أو سعر؟",
            ". لحظة أتأكدلك منه.",
        ):
            with self.subTest(tail=tail):
                trimmed = self._trim(denial + tail)
                self.assertIsNotNone(trimmed)
                self.assertTrue(trimmed.startswith(denial), trimmed)
                for word in ("Dior", "Bleu", "Million", "بديل", "أرشح", "شبه", "قريب", "تانية",
                             "أتأكدلك", "؟"):
                    self.assertNotIn(word, trimmed)

    def test_a_greeting_and_an_answered_side_question_survive(self):
        reply = ("وعليكم السلام يا فندم. للأسف لادور بخور مش موجود عندنا. "
                 "والشحن بيوصل خلال 3 أيام. تحب أرشحلك حاجة تانية؟")

        self.assertEqual(
            self._trim(reply),
            "وعليكم السلام يا فندم. للأسف لادور بخور مش موجود عندنا. والشحن بيوصل خلال 3 أيام.",
        )

    def test_a_reply_that_never_denies_is_not_kept(self):
        for reply in ("تقصد أنهي عطر وحجم؟", "لحظة أتأكدلك منه يا فندم.", "ok",
                      "عندنا Dior Homme Sport يا فندم."):
            with self.subTest(reply=reply):
                self.assertIsNone(self._trim(reply))

    def test_the_denied_name_itself_is_not_an_offer(self):
        reply = "للأسف Y Le Parfum مش موجود عندنا."

        self.assertEqual(self._trim(reply, asked="عندك Y Le Parfum؟"), reply)

    def test_a_denial_clause_that_names_something_unasked_is_not_kept(self):
        self.assertIsNone(self._trim("Dior Homme Sport مش موجود عندنا."))


@_confirm_pass_finds_nothing
class DenialSurvivesTheRouterTests(_Store):
    """Through `route`, where the model's prose is whatever the model felt like writing."""

    def test_a_pitched_alternative_never_reaches_the_customer(self):
        response, _, chat = self._route(
            "عندك لادور بخور ؟", _absent("لادور بخور"),
            "بعتذر يا فندم، لادور بخور مش موجود عندنا. بس عندنا Dior Homme Sport.")

        self.assertIn("مش موجود عندنا", response)
        self.assertNotIn("Dior Homme Sport", response)
        self.assertEqual(chat.call_count, 1)

    def test_an_offer_to_recommend_never_reaches_the_customer(self):
        response, _, _ = self._route(
            "عندك لادور بخور ؟", _absent("لادور بخور"),
            "للأسف لادور بخور مش موجود عندنا. تحب أرشحلك حاجة قريبة منه؟")

        self.assertEqual(response, "للأسف لادور بخور مش موجود عندنا.")

    def test_a_reply_with_no_denial_becomes_the_bare_denial(self):
        from products.services import product_info

        response, _, _ = self._route(
            "عندك لادور بخور ؟", _absent("لادور بخور"), "عندنا Dior Homme Sport يا فندم.")

        self.assertEqual(response, product_info.BARE_DENIAL)

    def test_an_invented_price_falls_back_to_the_denial_not_to_a_question(self):
        from products.services import product_info

        response, _, _ = self._route(
            "بكام لادور بخور ؟", _absent("لادور بخور"), "لادور بخور سعره 777 جنيه.")

        self.assertEqual(response, product_info.BARE_DENIAL)

    def test_a_chased_denial_is_said_again_without_a_second_generation(self):
        denial = "للأسف لادور بخور مش موجود عندنا يا فندم."
        self._route("عندك لادور بخور ؟", _absent("لادور بخور"), denial)

        response, context, chat = self._route("اتأكدلي منه", [], denial)

        self.assertIn("ABSENCE_DENIED", context)
        self.assertEqual(response, denial)
        self.assertEqual(chat.call_count, 1)

    def test_several_denials_in_a_row_do_not_turn_into_a_pitch(self):
        with mock.patch("products.services.fallback.suggest_alternatives") as suggest:
            for name in ("لادور بخور", "الكساندريا 2", "كريد افينتوس", "توم فورد عود"):
                response, context, _ = self._route(
                    f"عندك {name} ؟", _absent(name), "للأسف العطر ده مش موجود عندنا.")
                self.assertIn("ABSENCE_DENIED", context)
                self.assertIn("مش موجود عندنا", response)
        suggest.assert_not_called()

    def test_the_owner_is_told_the_bot_offered_nothing(self):
        self._route("عندك لادور بخور ؟", _absent("لادور بخور"), "للأسف لادور بخور مش موجود عندنا.")

        notification = Notification.objects.filter(store=self.store, type="handoff").latest("id")

        self.assertIn("مش موجود عندنا", notification.message)
        self.assertNotIn("وعرض عليه بدائل", notification.message)

    def test_the_third_press_still_hands_over(self):
        from products.services.router import HANDOFF_CONFIRMATION

        denial = "للأسف لادور بخور مش موجود عندنا يا فندم."
        self._route("عندك لادور بخور ؟", _absent("لادور بخور"), denial)
        self._route("بقول عندك لادور بخور ؟", _absent("لادور بخور"), denial)
        response, _, _ = self._route("يعني مفيش لادور بخور ؟", _absent("لادور بخور"), denial)

        self.assertTrue(self.conversation.needs_human)
        self.assertIn(HANDOFF_CONFIRMATION, response)

    def test_a_partial_denial_is_not_trimmed(self):
        reply = "Bleu de Chanel الـ50 ملي بـ645 جنيه. أما الكساندريا 2 فللأسف مش موجود عندنا."

        response, _, _ = self._route(
            "عايز اعرف اسعار بلو دي شانيل والكساندريا 2",
            Resolution([self.bleu], ("الكساندريا 2",)), reply)

        self.assertIn("Bleu de Chanel", response)
        self.assertIn("645", response)


class OtherUnavailableFlowsTests(_Store):
    def test_the_identification_flow_loads_no_perfume_to_pitch(self):
        from products.services import identification_service

        with mock.patch.object(
            identification_service, "extract_clues",
            return_value={"likely_known_perfume": "Creed Aventus"},
        ), mock.patch.object(
            identification_service, "score_candidates", return_value=[]
        ), mock.patch.object(identification_service, "chat", return_value="ok") as chat:
            _, context = identification_service.identify_perfume(
                "عطر فيه أناناس ودخان", [], self.store, self.conversation)

        prompt = chat.call_args[0][0][-1]["content"]
        self.assertEqual(context, "")
        self.assertNotIn("Dior Homme Sport", prompt)
        self.assertNotIn("ممكن تعرض ترشيح", prompt)
        self.assertIn("ممنوع ترشح", prompt)

    def test_the_order_flow_does_not_offer_to_find_a_substitute(self):
        import inspect

        from products.services import order_service

        source = inspect.getsource(order_service)
        self.assertNotIn("تحب نشوف بديل", source)
        self.assertNotIn("أرشحلك بدائل", source)

    def test_an_out_of_stock_perfume_is_not_a_reason_to_suggest_another(self):
        _, _, prompt, _ = self._info("Dior Homme Sport متوفر؟", [self.sport])

        self.assertNotIn("يسأل عن عطور تانية", prompt)
        self.assertIn("ممنوع تقترح عليه عطر تاني", prompt)
