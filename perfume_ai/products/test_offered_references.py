"""Comparison reference rows must never become offered-product selections."""

from django.test import TestCase

from products.models import Brand, Conversation, Message, Product, Store
from products.services.conversation_service import remember_offered, save_message
from products.services.sales import described


class OfferedReferenceTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Reference regression")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.reference = self.product("212 VIB Man")
        self.first = self.product("Sauvage Elixir")
        self.second = self.product("Fahrenheit")
        self.incidental = self.product("Dior Homme")
        self.conversation = Conversation.objects.create(store=self.store)
        self.names = [self.reference.name, self.first.name, self.second.name, self.incidental.name]
        self.context = (
            "Name (الاسم الصحيح): Sauvage Elixir\n"
            "Match: فيه شبه مع 212 VIB Man\n"
            "عطر مختلف عن: Dior Homme\n"
            "Name: Fahrenheit\n"
            "مش قريب من 212 VIB Man\n"
            + described.SIMILARITY_REFERENCE_HEADER + "\n"
            "Name (الاسم الصحيح): 212 VIB Man\nStock Status: متوفر\n"
        )
        self.reply = (
            "مفيش حاجة قريبة من 212 VIB Man بالظبط عندنا. "
            "Sauvage Elixir وFahrenheit مناسبين. تحب أقول لك الأسعار والأحجام؟"
        )

    def product(self, name):
        return Product.objects.create(store=self.store, brand=self.brand, name=name, gender="male")

    def test_shared_extractor_excludes_reference_and_incidental_context_names(self):
        self.assertEqual(
            described.supported_offered_names(self.reply, self.context, self.names),
            [self.first.name, self.second.name],
        )
        without_reference_row = self.context.partition(described.SIMILARITY_REFERENCE_HEADER)[0]
        self.assertEqual(
            described.supported_offered_names(
                self.reply + " Dior Homme مختلف.", without_reference_row, self.names,
            ),
            [self.first.name, self.second.name],
        )

    def test_reference_only_context_and_unspoken_candidates_offer_nothing(self):
        only_reference = self.context[self.context.index(described.SIMILARITY_REFERENCE_HEADER):]
        self.assertEqual(described.supported_offered_names(self.reply, only_reference, self.names), [])
        self.assertEqual(described.supported_offered_names("تمام", self.context, self.names), [])
        self.assertEqual(
            described.supported_offered_names("Dior Homme Intense", "Name: Dior Homme Intense", self.names), [],
        )

    def test_all_offer_readers_and_writer_share_the_candidate_roles(self):
        message = save_message(self.conversation, "assistant", self.reply, internal_context=self.context)
        expected = [self.first.name, self.second.name]
        self.assertEqual(described.offered_in_order(self.conversation, self.store, latest_only=True), expected)
        self.assertEqual(described.under_discussion(self.conversation, self.store), set(expected))
        self.assertEqual(described.offered_ever(self.conversation, self.store), set(expected))
        self.assertEqual(described.ordinal_products(self.conversation, self.store), expected)
        self.assertEqual(described.resolve_ordinal("الأول", self.conversation, self.store), self.first.name)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.sales_state["offered"], {
            "message_id": message.pk, "product_ids": [self.first.pk, self.second.pk],
        })

    def test_legacy_saved_offer_is_recomputed_from_its_source(self):
        source = Message.objects.create(
            conversation=self.conversation, role="assistant", content=self.reply, internal_context=self.context,
        )
        self.set_saved_offer(source, [self.reference.pk, self.second.pk, self.first.pk])
        self.add_neutral_replies()
        self.assertEqual(
            described.offered_in_order(self.conversation, self.store),
            [self.first.name, self.second.name],
        )

    def test_reference_does_not_resurface_from_an_earlier_real_offer(self):
        save_message(self.conversation, "assistant", self.reference.name,
                     internal_context="Name: " + self.reference.name)
        only_reference = self.context[self.context.index(described.SIMILARITY_REFERENCE_HEADER):]
        Message.objects.create(conversation=self.conversation, role="assistant",
                               content="مفيش حاجة قريبة من 212 VIB Man", internal_context=only_reference)
        self.assertEqual(described.offered_in_order(self.conversation, self.store), [])
        self.assertEqual(described.ordinal_products(self.conversation, self.store), [])
        # It really was offered earlier; the historical seen set must retain that fact.
        self.assertEqual(described.offered_ever(self.conversation, self.store), {self.reference.name})
        self.add_neutral_replies()
        self.assertEqual(described.offered_in_order(self.conversation, self.store), [])

    def test_saved_source_must_be_from_this_conversation_and_delivered_by_bot(self):
        other_conversation = Conversation.objects.create(store=self.store)
        for conversation, role, delivery in (
            (other_conversation, "assistant", "sent"),
            (self.conversation, "agent", "sent"),
            (self.conversation, "assistant", "pending"),
            (self.conversation, "assistant", "failed"),
        ):
            with self.subTest(role=role, delivery=delivery, conversation=conversation.pk):
                source = Message.objects.create(conversation=conversation, role=role, delivery_status=delivery,
                                                content=self.reply, internal_context=self.context)
                self.set_saved_offer(source, [self.first.pk])
                self.assertEqual(described.offered_in_order(self.conversation, self.store), [])
                previous = dict(self.conversation.sales_state)
                remember_offered(self.conversation, source)
                self.conversation.refresh_from_db()
                self.assertEqual(self.conversation.sales_state, previous)

    def test_store_mismatch_cannot_read_saved_or_recent_offers(self):
        save_message(self.conversation, "assistant", self.reply, internal_context=self.context)
        other_store = Store.objects.create(name="Another store")
        for read in (described.offered_in_order, described.under_discussion,
                     described.offered_ever, described.ordinal_products):
            with self.subTest(reader=read.__name__):
                self.assertFalse(read(self.conversation, other_store))

    def test_legacy_headers_headerless_contexts_and_cart_questions_still_work(self):
        for context in ("Name: Fahrenheit", "Name (الاسم الصحيح): Fahrenheit", "Fahrenheit"):
            with self.subTest(context=context):
                self.assertEqual(
                    described.supported_offered_names("Fahrenheit", context, self.names), [self.second.name],
                )
        self.assertEqual(
            described.supported_offered_names("Sauvage Elixir نوع الزجاجة؟", "No products found", self.names),
            [self.first.name],
        )
        Message.objects.create(conversation=self.conversation, role="assistant",
                               content="هسأل وأرد على Fahrenheit", internal_context="")
        self.assertEqual(described.under_discussion(self.conversation, self.store), {self.second.name})

    def set_saved_offer(self, source, product_ids):
        self.conversation.sales_state = {"offered": {"message_id": source.pk, "product_ids": product_ids}}
        self.conversation.save(update_fields=["sales_state"])

    def add_neutral_replies(self):
        for _ in range(2):
            Message.objects.create(conversation=self.conversation, role="assistant", content="تحت أمرك")
