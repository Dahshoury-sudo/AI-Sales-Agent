"""Public offer facts and the reported bundle question from conversation 1153.

Only the promotion section is needed: omit contact details and unrelated store
instructions. Keep the multiline layout and double spaces that broke quoting.
"""

BUNDLE_NAMES = (
    "Private Flame", "Smoked Velvet", "Sable Reserve",
    "Velvet Ember", "Velvet Vice", "Intense Icons",
)

BUNDLE_ENTRIES = (
    "باندل Private Flame\n Stronger (Intensly,Absolutely,Tobacco,Leather)\n"
    "الاربعة ب 3000 في الازايز المحاكاة الخاصة بينا و ب 4500  في ازايز اوريجنال",
    "باندل Smoked Velvet\n Tom Ford (Ombre Leather, Tobacco Vanilla, Vanilla Sex, Black Orchid)  \n"
    "الاربعة ب 3000 في الازايز المحاكاة الخاصة بينا و ب 4500  في ازايز اوريجنال",
    "باندل Sable Reserve\n(Alexandria 2, Layton De Marley, Imperial Valley, Black Afgano)\n"
    "الاربعة ب 5000 في الازايز المحاكاة الخاصة بينا و ب 6000  في ازايز اوريجنال",
    "عرض Velvet Ember \n(Spice Bomb, Stronger With You Intensly, Tom Ford Black Orchid, Dior Homme Intense)\n"
    "الاربعة ب 3000 في الازايز المحاكاة الخاصة بينا و ب 4500  في ازايز اوريجنال",
    "عرض Velvet Vice\n(Stronger With You Intensly, Tom Ford Black Orchid, Dior Homme Intense)\n"
    "3000 في الازايز الاوريجنال",
    "عرض Intense Icons\n(Ultra Male, Stronger With You Intensely, Spicebomb Extreme and Sauvage Elixir)\n"
    "ب 4500 في ازايز اوريجنال",
)

BUSINESS_FACTS = (
    "- عندنا حاليا 8 عروض وهما كالاتي:\n"
    "1- بوكس الصيف اشتري 3 حجم 90 مل عليهم 1 هدية 3000 جنيه 🎁\n"
    "2- الاتنين حجم 90 مل عليهم 50 مل هدية  2000 جنيه\n"
    + "\n".join(f"{index}- {entry}" for index, entry in enumerate(BUNDLE_ENTRIES, 3))
    + "\n\nهذة العروض متاحة للطلب من خلال الويبسايت او احدي منصات التواصل الاجتماعي فقط وليس من خلال البوت الذكي"
)

PRIOR_OFFER_REPLY = (
    "تفاصيل العرض:\n1- بوكس الصيف اشتري 3 حجم 90 مل عليهم 1 هدية 3000 جنيه 🎁\n"
    "2- الاتنين حجم 90 مل عليهم 50 مل هدية  2000 جنيه\n\n"
    "تسجيل طلبات الباندلز والكومبوهات أو تطبيق العروض والخصومات والكوبونات "
    "والهدايا الترويجية مش بقدر أعمله هنا؛ ده لازم يتم مع فريق خدمة العملاء.\n\n"
    "تحب أحولك لمندوب يساعدك؟"
)
HISTORY = [
    {"role": "user", "content": "في عروض ؟"},
    {"role": "assistant", "content": PRIOR_OFFER_REPLY},
]
QUESTION = "في باندل ؟"
