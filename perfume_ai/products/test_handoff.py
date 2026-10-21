"""Human transfers close clearly, preserve follow-ups, and never start checkout."""
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from products.models import Cart, Conversation, Message, Notification, StaticFAQ, Store, StoreSettings
from products.services.conversation_service import build_llm_history, receive_event, save_message
from products.services.handoff import handoff_offer_reply
from products.services.router import route


OFFER_1122 = (
    "عرض Velvet Vice متاح بس في أزايز الأوريجينال، مش في أزايز البراند الخاصة بينا. \n\n"
    "أنا كمساعد آلي مش بقدر أطبق أو أنفذ العروض بنفسي، ده شغل المندوب البشري. "
    "لو تحب، أقدر أحولك لمندوب بشري يتابع معاك التفاصيل ويطبق العرض. تحب؟"
)
ACKNOWLEDGMENT = "حولت المحادثة لفريق خدمة العملاء، وهما هيتابعوا معاك."


def bot(content):
    return {"role": "assistant", "content": content}


class HandoffConsentTests(SimpleTestCase):
    def test_short_approvals_and_spelling_variants(self):
        for reply in ("ماشي", "تمام", "آه", "أيوه", "ايوة", "ايوا", "نعم", "أُوكْ", "حاضر",
                      "yes", "YES!", "Okay.", "sure", "yes please", "تمام، شكراً", "أيوه 👍"):
            with self.subTest(reply=reply):
                self.assertIs(handoff_offer_reply(reply, [bot(OFFER_1122)]), True)

    def test_handoff_is_independent_of_topic_and_language(self):
        offers = (
            OFFER_1122,
            "المسكات من تخصص المندوب. تحب أحولك لمندوب بشري يساعدك؟",
            "تحب تتكلم مع حد حقيقي؟",
            "أقدر أوصلك بخدمة العملاء لو حابب.",
            "Would you like to speak to a human?",
            "Do you want to talk to a real human?",
            "I can connect you to our support team. Would you like that?",
            "Can I connect you to a human? They can answer your question.",
            "Would you like me to put you in touch with customer service?",
            "تحب أكلم لك مندوب؟ هيساعدك في تفاصيل العرض.",
        )
        for offer in offers:
            with self.subTest(offer=offer):
                self.assertIs(handoff_offer_reply("yes", [bot(offer)]), True)

    def test_acceptance_with_question_is_immediate(self):
        for reply in ("yes, and ask them about the price", "ماشي واسأله عن سعر العرض",
                      "تمام بس الشحن بكام؟", "yes, I don't need the perfume, ask about delivery",
                      "حولني", "حوّلني لمندوب", "please do", "go ahead"):
            with self.subTest(reply=reply):
                self.assertIs(handoff_offer_reply(reply, [bot(OFFER_1122)]), True)

    def test_refusals_postponements_and_bot_insistence_are_not_consent(self):
        for reply in ("لا", "لأ شكرا", "no thanks", "مش دلوقتي", "yes but not now",
                      "تمام بس استنى", "ماشي بس جاوبني عن السعر الاول", "yes, answer the price first",
                      "اه بس مش عايز مندوب", "تمام بس متحولنيش", "yes, don't transfer me",
                      "yes but I don't want a human", "تمام بس عايزك انت تنفذ العرض",
                      "yes okay but wait", "اه تمام بس مش دلوقتي", "مش عايز", "بلاش شكرا",
                      "yes, I want you to apply the offer", "لا انا عايزك انت تنفذه"):
            with self.subTest(reply=reply):
                self.assertIs(handoff_offer_reply(reply, [bot(OFFER_1122)]), False)

    def test_approval_is_not_a_substring(self):
        for reply in ("معاه", "مياه", "تماما مختلف", "smoky", "yesterday", "بكام العرض؟"):
            with self.subTest(reply=reply):
                self.assertIsNone(handoff_offer_reply(reply, [bot(OFFER_1122)]))

    def test_no_offer_or_superseded_offer(self):
        histories = (
            [],
            [bot("تحب أقولك الأسعار والأحجام؟")],
            [bot(OFFER_1122), {"role": "user", "content": "بكام سوفاج؟"}, bot("تحب أقولك الأحجام؟")],
            [bot("ممكن أحولك لمندوب بشري. تحب أقولك سعر سوفاج؟")],
            [bot("تحب أحولك لمندوب ولا أقولك الأسعار؟")],
            [bot("Would you like a transfer to a human or would you like to see the prices?")],
            [bot("تحب أحولك لمندوب؟ انهي عطر عجبك")],
            [bot("حولت المحادثة لفريق خدمة العملاء.")],
            [bot("العروض من تخصص المندوب البشري.")],
            [bot("مش بقدر أحولك لمندوب بشري.")],
            [bot("تحب تعرف ليه ماقدرش أحولك لمندوب؟")],
            [bot("I can't transfer you to a human.")],
            [{"role": "user", "content": OFFER_1122}],
            [bot(OFFER_1122), {"role": "agent", "content": "تحب حجم 50 مل؟"}],
        )
        for history in histories:
            with self.subTest(history=history):
                self.assertIs(handoff_offer_reply("yes", history), False)


class HandoffRoutingTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Handoff regression")
        StoreSettings.objects.create(store=self.store)
        self.conversation = Conversation.objects.create(store=self.store)
        self.client = APIClient()
        self.client.credentials(HTTP_X_API_KEY=self.store.api_key)
        self.addCleanup(patch.stopall)
        self.general = patch("products.services.router.handle_general", return_value=(ACKNOWLEDGMENT, "")).start()
        self.usage = patch("products.services.router.record_llm_message").start()

    def accept(self, reply="ماشي", history=None, conversation=None):
        with patch("products.services.router.classify", side_effect=AssertionError("Consent must bypass classification")), \
             patch("products.services.router.handle_order", side_effect=AssertionError("Consent must bypass checkout")):
            return route(reply, history if history is not None else [bot(OFFER_1122)],
                         self.store, conversation or self.conversation)

    def assert_handed_off(self):
        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.needs_human)
        self.assertEqual(Notification.objects.filter(store=self.store, type="handoff").count(), 1)
        self.assertFalse(Cart.objects.filter(conversation=self.conversation).exists())

    def test_conversation_1122_transfers_without_classification_or_order(self):
        self.assertEqual(self.accept(), (ACKNOWLEDGMENT, ""))
        self.assert_handed_off()
        self.general.assert_not_called()
        self.usage.assert_not_called()

    def test_active_cart_faq_and_original_preference_cannot_intercept(self):
        cart = Cart.objects.create(conversation=self.conversation, customer_name="Original")
        self.conversation.preferences = {"requires_original_liquid": True}
        self.conversation.save(update_fields=["preferences"])
        StaticFAQ.objects.create(store=self.store, question="ack", keywords="ماشي", answer="FAQ")
        reply = "ماشي خلي المندوب يقولي الشحن بكام"
        self.assertEqual(self.accept(reply), (ACKNOWLEDGMENT, ""))
        self.conversation.refresh_from_db()
        cart.refresh_from_db()
        self.assertTrue(self.conversation.needs_human)
        self.assertEqual(cart.customer_name, "Original")
        self.general.assert_not_called()

    def test_short_faq_match_cannot_intercept(self):
        StaticFAQ.objects.create(store=self.store, question="ack", keywords="yes", answer="FAQ")
        self.accept("yes")
        self.assert_handed_off()

    def test_refusal_blocks_even_an_incorrect_handoff_classification(self):
        with patch("products.services.router.classify", return_value="handoff"):
            route("yes but not now", [bot(OFFER_1122)], self.store, self.conversation)
        self.conversation.refresh_from_db()
        self.assertFalse(self.conversation.needs_human)
        self.assertFalse(Notification.objects.exists())

    def test_ordinary_agreement_does_not_revive_an_old_offer(self):
        with patch("products.services.router.classify", return_value="handoff"):
            route("ماشي", [bot(OFFER_1122), bot("تحب أقولك الأسعار؟")], self.store, self.conversation)
        self.assertFalse(Notification.objects.exists())

    def test_legacy_topic_classifications_cannot_treat_refusal_as_acceptance(self):
        for intent in ("promotion", "musk_mix_product"):
            with self.subTest(intent=intent), patch("products.services.router.classify", return_value=intent):
                route("تمام بس مش عايز مندوب", [bot(OFFER_1122)], self.store, self.conversation)
                self.conversation.refresh_from_db()
                self.assertFalse(self.conversation.needs_human)
                self.assertFalse(Notification.objects.exists())

    def test_activation_is_idempotent_even_with_a_stale_instance(self):
        stale = Conversation.objects.get(pk=self.conversation.pk)
        self.accept()
        self.accept(conversation=stale)
        self.assert_handed_off()

    def test_fresh_offer_can_reopen_a_resolved_handoff(self):
        self.accept()
        Conversation.objects.filter(pk=self.conversation.pk).update(needs_human=False)
        self.conversation.refresh_from_db()
        history = [bot(ACKNOWLEDGMENT), {"role": "user", "content": "محتاج مساعدة تاني"}, bot(OFFER_1122)]
        self.accept(history=history)
        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.needs_human)
        self.assertEqual(Notification.objects.filter(type="handoff").count(), 2)

    def test_longer_classified_acceptance_can_reopen_handoff(self):
        with patch("products.services.router.classify", return_value="handoff"):
            reply = route("That would be helpful, I'd appreciate speaking with them",
                          [bot(ACKNOWLEDGMENT), bot(OFFER_1122)], self.store, self.conversation)
        self.assertEqual(reply, (ACKNOWLEDGMENT, ""))
        self.general.assert_not_called()
        self.usage.assert_called_once_with(self.store)
        self.assert_handed_off()

    def test_explicit_request_still_activates_handoff(self):
        with patch("products.services.router.classify", return_value="handoff"):
            reply = route("حولني لمندوب بشري", [], self.store, self.conversation)
        self.assertEqual(reply, (ACKNOWLEDGMENT, ""))
        self.general.assert_not_called()
        self.usage.assert_called_once_with(self.store)
        self.assert_handed_off()

    def test_unsent_offer_is_not_used_as_context(self):
        save_message(self.conversation, "assistant", "تحب أقولك الأسعار؟")
        save_message(self.conversation, "assistant", OFFER_1122, delivery_status="failed")
        with patch("products.services.router.classify", return_value="faq"):
            route("yes", build_llm_history(self.conversation), self.store, self.conversation)
        self.assertFalse(Notification.objects.exists())

    def test_staff_reply_supersedes_offer_even_when_filtered_from_llm_history(self):
        save_message(self.conversation, "assistant", OFFER_1122)
        save_message(self.conversation, "agent", "تحب حجم 50 مل؟")
        history = build_llm_history(self.conversation)
        self.assertEqual(history, [bot(OFFER_1122)])
        with patch("products.services.router.classify", return_value="handoff"):
            route("yes", history, self.store, self.conversation)
        self.conversation.refresh_from_db()
        self.assertFalse(self.conversation.needs_human)
        self.assertFalse(Notification.objects.exists())

    def test_web_retry_and_followup_preserve_human_workflow(self):
        from products.views import signer

        save_message(self.conversation, "assistant", OFFER_1122)
        payload = {"conversation_id": signer.sign_object(self.conversation.pk),
                   "message": "ماشي، وسعر العرض كام؟", "client_message_id": "consent-1122"}
        with patch("products.services.router.classify", side_effect=AssertionError("No classification")):
            first = self.client.post("/api/chat/", payload)
            retry = self.client.post("/api/chat/", payload)
            followup = self.client.post("/api/chat/", {**payload, "message": "سعر العرض كام؟", "client_message_id": "after-consent"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(retry.data, first.data)
        self.assertTrue(first.data["needs_human"])
        self.assertEqual(first.data["reply"], ACKNOWLEDGMENT)
        self.assertEqual(followup.data["reply"], "")
        self.assertTrue(followup.data["needs_human"])
        self.assertEqual(Message.objects.filter(conversation=self.conversation, role="user").count(), 2)
        self.assertEqual(Message.objects.get(pk=first.data["user_message_id"]).content, payload["message"])
        self.assertEqual(Message.objects.get(pk=first.data["message_id"]).content, ACKNOWLEDGMENT)
        self.general.assert_not_called()
        self.usage.assert_not_called()
        self.assert_handed_off()

    def test_conversations_1062_and_1071_end_with_only_the_transfer_confirmation(self):
        from products.views import signer

        cases = (
            ("1062", "تحب أحولك لمندوب يتابع معاك الموضوع؟", "ياريت",
             ("في رقم وتس للتواصل", "في رقم تواصل")),
            ("1071", None, "وصلني بحد من خدمة العملاء", ("تمام شكرا",)),
        )
        for number, (label, offer, request, followups) in enumerate(cases, 1):
            with self.subTest(conversation=label):
                conversation = Conversation.objects.create(store=self.store)
                if offer:
                    save_message(conversation, "assistant", offer)
                payload = {"conversation_id": signer.sign_object(conversation.pk),
                           "message": request, "client_message_id": label}
                self.usage.reset_mock()
                with patch("products.services.router.classify", return_value="handoff") as classify:
                    first = self.client.post("/api/chat/", payload)
                    retry = self.client.post("/api/chat/", payload)
                    for index, followup in enumerate(followups):
                        response = self.client.post("/api/chat/", {
                            **payload, "message": followup, "client_message_id": f"{label}-{index}",
                        })
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.data["reply"], "")
                        self.assertTrue(response.data["needs_human"])
                self.assertEqual(first.status_code, 200)
                self.assertEqual(retry.data, first.data)
                self.assertTrue(first.data["needs_human"])
                self.assertEqual(first.data["reply"], ACKNOWLEDGMENT)
                self.assertEqual(Message.objects.get(pk=first.data["message_id"]).content, ACKNOWLEDGMENT)
                self.assertEqual(list(conversation.messages.filter(role="user").order_by("id")
                                      .values_list("content", flat=True)), [request, *followups])
                self.assertEqual(conversation.messages.filter(role="assistant").count(), 1 + bool(offer))
                self.assertEqual(Notification.objects.filter(store=self.store, type="handoff").count(), number)
                classify.assert_called_once()
                self.usage.assert_called_once_with(self.store)
                self.general.assert_not_called()

    def test_messenger_transfer_and_delivery_retry(self):
        from products.tasks import process_incoming_message

        self.conversation.platform = "messenger"
        self.conversation.platform_sender_id = "handoff-customer"
        self.conversation.save(update_fields=["platform", "platform_sender_id"])
        save_message(self.conversation, "assistant", OFFER_1122)
        event = receive_event(self.store, "messenger", "handoff-customer", "yes", "consent-event", conversation=self.conversation)
        with patch("products.services.router.classify", side_effect=AssertionError("No classification")), \
             patch("products.tasks.rate_limit.hit_all", return_value=(True, 0, None)), \
             patch("products.tasks.send_platform_message", return_value=True) as send:
            for _ in range(2):
                process_incoming_message.run(self.store.pk, "messenger", "handoff-customer", "yes", event_id=event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, "complete")
        self.assertEqual(event.reply_message.content, ACKNOWLEDGMENT)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[1], ACKNOWLEDGMENT)
        self.general.assert_not_called()
        self.usage.assert_not_called()
        self.assert_handed_off()

    def test_failed_reply_processing_rolls_back_activation_and_notification(self):
        from products.services.conversation_service import generate_event

        save_message(self.conversation, "assistant", OFFER_1122)
        event = receive_event(self.store, "web", "", "yes", "failed-consent", conversation=self.conversation)
        def fail_processing(reply, conversation):
            self.assertEqual(reply, ACKNOWLEDGMENT)
            self.assertTrue(conversation.needs_human)
            self.assertTrue(Notification.objects.filter(store=self.store, type="handoff").exists())
            raise RuntimeError("reply processing failed")

        with self.assertRaisesRegex(RuntimeError, "reply processing failed"):
            generate_event(event, route, fail_processing)
        self.conversation.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(event.status, "pending")
        self.assertIsNone(event.reply_message_id)
        self.assertFalse(self.conversation.needs_human)
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Message.objects.filter(role="user").exists())
