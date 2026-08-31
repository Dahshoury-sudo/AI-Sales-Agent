"""Turn free-text perfume notes into something comparable.

Notes reach the database as whatever the store owner typed into columns K/L/M of the
bulk-import sheet (products/services/bulk_import.py): "Citrus, Mint", "عود وفانيليا",
"Bergamot/Pepper". Nothing normalises them, so two perfumes that genuinely share a
note often do not share a *string*, and comparing them literally finds far less
overlap than actually exists.

Hence two layers of normalisation: the note itself, and the accord family it belongs
to. Family overlap is what lets "bergamot" and "lemon" count as related. Without it,
similarity scoring over a real catalogue is too sparse to rank anything — which is
the state the search path was in, where the only accord that expanded at all was
"sweet".

A note may belong to more than one family on purpose: cherry is both fruity and
gourmand, and forcing a single label would lose whichever half the customer meant.
"""

import re

from ..static_faq_service import normalize_arabic

# Free text separators. Arabic "و" (and) is only treated as a separator when it stands
# alone — attached to the following word ("وفانيليا") it is part of the note, so
# splitting on a bare و would corrupt it.
_SEPARATORS = re.compile(r"[,،;/|+\n\r]+|\s+و\s+|\s+and\s+", re.IGNORECASE)


def parse_notes(text):
    """Split one note field into normalised note names, order preserved.

    Returns a tuple so the result can be cached and cannot be mutated by a caller.
    """
    if not text:
        return ()

    seen = []
    for piece in _SEPARATORS.split(str(text)):
        note = normalize_arabic(piece)
        # normalize_arabic lowercases and folds alef/ya/ta-marbuta, so "Bergamot" and
        # "bergamot" and "الياسمين"/"الياسمين" converge here rather than at every
        # comparison site.
        note = note.strip(" .-_\t")
        if note and note not in seen:
            seen.append(note)
    return tuple(seen)


# The sweet-request expansion that used to live inline in search_service.py. Kept
# byte-identical to preserve the existing "عايز حاجة مسكرة" behaviour exactly; the
# richer gourmand family below is additive and used only by the new scoring path.
SWEET_NOTE_EXPANSION = (
    "vanilla", "caramel", "tonka", "praline", "honey", "chocolate",
    "cacao", "marshmallow", "sugar", "cherry", "plum",
)

# What a customer says when they are asking for a sweet perfume rather than naming a
# note. Also unchanged from search_service.py.
SWEET_REQUEST_TERMS = ("sweet", "gourmand", "مسكر", "سويتي")

# The same idea for "فريش". This one exists because `notes: ["fresh"]` was worse than
# useless: no product has a note literally called "fresh", so the term matched nothing,
# and since notes are a hard filter on the `exact` queryset it silently emptied the
# matched set. A customer asking for a fresh summer perfume got zero exact matches and
# fell through to the alternatives branch, which is instructed never to say "لا يوجد".
FRESH_REQUEST_TERMS = ("fresh", "فريش", "منعش", "منعشه", "منعشة")
FRESH_NOTE_EXPANSION = (
    "bergamot", "lemon", "lime", "grapefruit", "mandarin", "orange", "neroli",
    "citrus", "mint", "marine", "aquatic", "sea salt", "calone", "green notes",
    "green tea", "lavender", "petitgrain",
)

# Incense, and every word a customer or a store uses for it. Conversation 795's customer asked
# for بخور; this catalogue types its notes in English; `expand_request_term` matches literally —
# so the request scored 0.0 against every perfume in the store, including the two built on
# frankincense, and the price sort underneath handed back the cheapest bottle in the catalogue.
#
# Unlike the two groups above, this is one ingredient rather than an accord, so the expansion is
# the ingredient's synonyms and NOT its family. `بخور` reads as `amber`, and so do amberwood,
# labdanum, benzoin and ambroxan — expanding to the family would have made Stronger With You a
# valid answer to an incense request all over again, on the strength of one amberwood note.
# Myrrh and opoponax are in because a bakhoor blend is those resins; عنبر is out because amber
# is a different material that merely shares the family.
INCENSE_REQUEST_TERMS = ("incense", "frankincense", "olibanum", "بخور", "لبان")
INCENSE_NOTE_EXPANSION = (
    "incense", "frankincense", "olibanum", "myrrh", "opoponax", "بخور", "لبان",
)


# Request term -> the notes it should be searched and scored as. One table so the SQL
# filter and the Python ranker cannot drift apart: they did, and the consequence was
# that "عايزه عطر مسكر اوي" filtered correctly to the gourmands and then scored all
# seven of them identically with no reasons, so the shortlist fell back to bulk-oil
# order and a white floral was recommended for a dessert-sweet request.
_REQUEST_EXPANSIONS = (
    (SWEET_REQUEST_TERMS, SWEET_NOTE_EXPANSION),
    (FRESH_REQUEST_TERMS, FRESH_NOTE_EXPANSION),
    (INCENSE_REQUEST_TERMS, INCENSE_NOTE_EXPANSION),
)


def expand_request_term(term):
    """The note names a requested term should match, as a tuple.

    An ordinary ingredient ("vanilla") returns just itself. An accord word ("مسكر",
    "فريش") returns the family it stands for.
    """
    lowered = str(term or "").strip().lower()
    if not lowered:
        return ()
    for triggers, expansion in _REQUEST_EXPANSIONS:
        if lowered in triggers:
            return tuple(expansion)
    return (lowered,)


# note -> the families it reads as. Deliberately not exhaustive perfumery taxonomy:
# it covers what stores actually type, in English (the import template's language) and
# in Arabic (what they type by hand).
NOTE_FAMILIES = {
    # citrus
    "bergamot": ("citrus",), "lemon": ("citrus",), "lime": ("citrus",),
    "orange": ("citrus",), "bitter orange": ("citrus",), "blood orange": ("citrus",),
    "grapefruit": ("citrus",), "mandarin": ("citrus",), "tangerine": ("citrus",),
    "neroli": ("citrus", "floral"), "petitgrain": ("citrus", "green"),
    "yuzu": ("citrus",), "citrus": ("citrus",), "حمضيات": ("citrus",),
    "برجموت": ("citrus",), "ليمون": ("citrus",), "برتقال": ("citrus",),
    # aromatic / herbal
    "lavender": ("aromatic",), "rosemary": ("aromatic",), "sage": ("aromatic",),
    "clary sage": ("aromatic",), "basil": ("aromatic",), "mint": ("aromatic",),
    "peppermint": ("aromatic",), "spearmint": ("aromatic",), "thyme": ("aromatic",),
    "eucalyptus": ("aromatic",), "geranium": ("aromatic", "floral"),
    "aromatic": ("aromatic",), "لافندر": ("aromatic",), "نعناع": ("aromatic",),
    "ريحان": ("aromatic",),
    # woody
    "cedar": ("woody",), "cedarwood": ("woody",), "sandalwood": ("woody",),
    "vetiver": ("woody",), "patchouli": ("woody",), "oakmoss": ("woody", "green"),
    "guaiac": ("woody",), "guaiac wood": ("woody",), "cypress": ("woody",),
    "pine": ("woody",), "fir": ("woody",), "birch": ("woody", "leather"),
    "oud": ("woody", "amber"), "agarwood": ("woody", "amber"),
    "teak": ("woody",), "papyrus": ("woody",), "moss": ("woody", "green"),
    "woody": ("woody",), "wood": ("woody",),
    "خشب": ("woody",), "خشبي": ("woody",), "صندل": ("woody",),
    "عود": ("woody", "amber"), "باتشولي": ("woody",), "فيتيفر": ("woody",),
    "ارز": ("woody",),
    # amber / resinous / incense
    "amber": ("amber",), "ambroxan": ("amber", "musk"), "ambergris": ("amber",),
    "labdanum": ("amber",), "benzoin": ("amber", "gourmand"), "resin": ("amber",),
    "incense": ("amber",), "frankincense": ("amber",), "olibanum": ("amber",),
    "myrrh": ("amber",), "opoponax": ("amber",), "tolu balsam": ("amber",),
    "عنبر": ("amber",), "لبان": ("amber",), "بخور": ("amber",),
    # gourmand
    "vanilla": ("gourmand",), "caramel": ("gourmand",), "tonka": ("gourmand",),
    "tonka bean": ("gourmand",), "praline": ("gourmand",), "honey": ("gourmand",),
    "chocolate": ("gourmand",), "cacao": ("gourmand",), "cocoa": ("gourmand",),
    "marshmallow": ("gourmand",), "sugar": ("gourmand",), "toffee": ("gourmand",),
    "coffee": ("gourmand",), "almond": ("gourmand",), "hazelnut": ("gourmand",),
    "coconut": ("gourmand", "fruity"), "rum": ("gourmand",), "whiskey": ("gourmand",),
    "cherry": ("fruity", "gourmand"), "plum": ("fruity", "gourmand"),
    "gourmand": ("gourmand",), "sweet": ("gourmand",),
    "فانيليا": ("gourmand",), "كراميل": ("gourmand",), "قهوه": ("gourmand",),
    "شوكولاته": ("gourmand",), "عسل": ("gourmand",), "لوز": ("gourmand",),
    # floral
    "rose": ("floral",), "jasmine": ("floral",), "tuberose": ("floral",),
    "ylang": ("floral",), "ylang-ylang": ("floral",), "iris": ("floral", "powdery"),
    "orris": ("floral", "powdery"), "violet": ("floral", "powdery"),
    "peony": ("floral",), "lily": ("floral",), "lily of the valley": ("floral",),
    "muguet": ("floral",), "freesia": ("floral",), "gardenia": ("floral",),
    "magnolia": ("floral",), "orange blossom": ("floral", "citrus"),
    "osmanthus": ("floral", "fruity"), "mimosa": ("floral",),
    "narcissus": ("floral",), "heliotrope": ("floral", "powdery"),
    "floral": ("floral",), "flowers": ("floral",),
    "ورد": ("floral",), "جاسمين": ("floral",), "ياسمين": ("floral",),
    "زهور": ("floral",), "بنفسج": ("floral", "powdery"), "فل": ("floral",),
    # spicy
    "pepper": ("spicy",), "black pepper": ("spicy",), "pink pepper": ("spicy",),
    "cardamom": ("spicy",), "cinnamon": ("spicy",), "clove": ("spicy",),
    "nutmeg": ("spicy",), "ginger": ("spicy",), "saffron": ("spicy",),
    "cumin": ("spicy",), "coriander": ("spicy",), "anise": ("spicy",),
    "star anise": ("spicy",), "juniper": ("spicy", "aromatic"),
    "spicy": ("spicy",), "spices": ("spicy",),
    "فلفل": ("spicy",), "قرفه": ("spicy",), "زعفران": ("spicy",),
    "هيل": ("spicy",), "جنزبيل": ("spicy",), "توابل": ("spicy",),
    # leather / smoky
    "leather": ("leather",), "suede": ("leather",), "birch tar": ("leather",),
    "tobacco": ("leather", "gourmand"), "hay": ("leather", "green"),
    "smoke": ("leather",), "smoky": ("leather",),
    "جلد": ("leather",), "تبغ": ("leather",), "دخان": ("leather",),
    # musk / animalic
    "musk": ("musk",), "white musk": ("musk",), "civet": ("musk",),
    "castoreum": ("musk", "leather"), "ambrette": ("musk",),
    "مسك": ("musk",),
    # aquatic
    "marine": ("aquatic",), "aquatic": ("aquatic",), "sea notes": ("aquatic",),
    "sea salt": ("aquatic",), "salt": ("aquatic",), "calone": ("aquatic",),
    "water notes": ("aquatic",), "ozonic": ("aquatic",), "ozone": ("aquatic",),
    "seaweed": ("aquatic", "green"),
    "بحري": ("aquatic",), "مياه": ("aquatic",),
    # fruity
    "apple": ("fruity",), "pear": ("fruity",), "pineapple": ("fruity",),
    "peach": ("fruity",), "apricot": ("fruity",), "raspberry": ("fruity",),
    "strawberry": ("fruity",), "blackcurrant": ("fruity",), "cassis": ("fruity",),
    "melon": ("fruity",), "watermelon": ("fruity",), "fig": ("fruity", "green"),
    "grape": ("fruity",), "lychee": ("fruity",), "mango": ("fruity",),
    "passionfruit": ("fruity",), "berry": ("fruity",), "fruity": ("fruity",),
    "تفاح": ("fruity",), "اناناس": ("fruity",), "خوخ": ("fruity",),
    "فراوله": ("fruity",), "فواكه": ("fruity",),
    # green / tea
    "green notes": ("green",), "grass": ("green",), "galbanum": ("green",),
    "violet leaf": ("green",), "fig leaf": ("green",), "tea": ("green",),
    "green tea": ("green",), "tomato leaf": ("green",), "ivy": ("green",),
    "bamboo": ("green",), "green": ("green",),
    "اخضر": ("green",), "شاي": ("green",),
    # powdery
    "powdery": ("powdery",), "aldehydes": ("powdery",), "aldehyde": ("powdery",),
    "rice powder": ("powdery",), "talc": ("powdery",),
    "بودره": ("powdery",),
}


# Shortest key allowed to match as a bare substring in the tolerant pass below. "rum" is
# inside "geranium" and "tea" inside "steam", so the short keys are exact/word-only; four
# characters is long enough that "amber" reaching into "amberwood" is the intended reading
# rather than a collision.
_SUBSTRING_KEY_FLOOR = 4

_WORD_SPLIT = re.compile(r"[^\w؀-ۿ]+")


def note_families(note, tolerant=False):
    """The families one note reads as.

    Exact lookup only, unless `tolerant`. The tolerant pass exists because store-typed
    notes are frequently a known ingredient wearing an adjective — "marine notes",
    "sicilian lemon", "woody notes", "amberwood" — and an exact lookup scores every one of
    them as an unknown string with no family at all. Invictus is the case that matters:
    its `marine notes` yielded no `aquatic`, so the one genuine aquatic in the catalogue
    read as having no aquatic character.

    Three passes, narrowest first, stopping at the first that finds anything — so a note
    whose own name is in the table is never re-read as something else. "pineapple leaf"
    resolves through its word `pineapple` and never reaches the substring pass, where
    `pine` would have made it woody.
    """
    exact = NOTE_FAMILIES.get(note)
    if exact:
        return frozenset(exact)
    if not tolerant:
        return frozenset()

    found = set()
    for word in _WORD_SPLIT.split(note):
        found.update(NOTE_FAMILIES.get(word, ()))
    if found:
        return frozenset(found)

    for key, value in NOTE_FAMILIES.items():
        if len(key) >= _SUBSTRING_KEY_FLOOR and key in note:
            found.update(value)
    return frozenset(found)


def families(notes, tolerant=False):
    """The accord families a set of notes reads as.

    Unknown notes contribute nothing rather than a catch-all family: an unrecognised
    string is genuinely unknown, and bucketing it would invent similarity.

    `tolerant` is opt-in rather than the default because the callers divide on it.
    Similarity, the `avoid_heavy` penalty and `value.py` compare two products against each
    other, and there a missed family costs both sides equally; the note-fit scorer asks how
    much of *one* perfume is a requested accord, and there a missed family is the whole
    answer. Flipping the default would move similarity bands that are tuned against this
    catalogue.
    """
    found = set()
    for note in notes:
        found.update(note_families(note, tolerant=tolerant))
    return frozenset(found)


# Arabic proclitics a customer's wording carries and a note field does not. "البخور",
# "وبخور" and "بالعود" are the same request as "بخور" and "عود", but `normalize_arabic`
# folds letters only — it does not strip these — so an exact lookup misses all three.
# Longest first, so "وبال" is consumed whole rather than leaving "بالعود" behind.
_PROCLITICS = ("وبال", "فبال", "بال", "وال", "فال", "لل", "ال", "و", "ف", "ب", "ل")

# Shortest form allowed to match a word in customer prose. The only table key below it is
# "فل" (jasmine), and in a sentence that is far likelier to be a stray syllable than a note
# request — prose is full of two-letter words, a store-typed note field is not.
_PROSE_KEY_FLOOR = 3


def _prose_candidates(word):
    """The word itself, then each form left by stripping one leading proclitic.

    The bare word is yielded first so a note that merely *starts* with a proclitic is never
    mangled into something else: "لبان" is frankincense, and stripping its "ل" would leave
    "بان", which is nothing.
    """
    yield word
    for clitic in _PROCLITICS:
        if word.startswith(clitic) and len(word) > len(clitic):
            yield word[len(clitic):]


def terms_in(text):
    """The note and accord terms a customer's own message asks for, order preserved.

    `parse_notes` reads a store-typed note *field*; this reads a *message*, and the two need
    different rules. A field is a list of ingredients, so every piece of it is a note. A
    sentence is not: only the words that happen to be in the table are, and they arrive
    wearing the definite article and whatever conjunction preceded them.

    Exact lookup only — never `note_families(tolerant=True)`. That pass exists to find
    "amberwood" inside a note field, and its substring stage turned loose on prose reads
    "الاسعار" and "عندنا" as ingredients.

    A perfume name that happens to contain a note word resolves through it on purpose: a
    customer asking for a "Tobacco Vanille" we do not stock is best answered with the
    closest tobacco gourmand we do.

    Exists for conversation 795. "عندكو لادور بخور صح ؟" named a bakhoor the store does not
    carry, and `fallback.suggest_alternatives` — which ranked on price alone — answered with
    the cheapest perfume in the catalogue. Two genuine incense perfumes were sitting in it.
    """
    found = []
    for word in _WORD_SPLIT.split(normalize_arabic(text or "")):
        for candidate in _prose_candidates(word):
            if len(candidate) < _PROSE_KEY_FLOOR:
                continue
            if (
                candidate in NOTE_FAMILIES
                or candidate in SWEET_REQUEST_TERMS
                or candidate in FRESH_REQUEST_TERMS
            ):
                if candidate not in found:
                    found.append(candidate)
                break
    return tuple(found)


# What "تقيلة" / "تخنق" means in data terms. Used as a penalty when the customer asks
# to avoid a heavy scent, not as a filter — a perfume with one amber note is not
# automatically suffocating.
HEAVY_FAMILIES = frozenset({"amber", "leather", "gourmand"})
HEAVY_NOTES = frozenset({
    "oud", "agarwood", "عود", "incense", "بخور", "frankincense", "myrrh",
    "labdanum", "patchouli", "باتشولي", "leather", "جلد", "tobacco", "تبغ",
    "amber", "عنبر", "musk", "مسك",
})

# The reverse: what reads as light and airy, used as a bonus for the same request.
LIGHT_FAMILIES = frozenset({"citrus", "aquatic", "green", "aromatic"})

# What an accord word *means*, stated rather than derived from its note expansion.
#
# Deriving it was a mistake worth naming: `families(FRESH_NOTE_EXPANSION)` picks up `floral`,
# because `neroli` is legitimately both citrus and floral. So `floral` became a fresh family,
# and Le Male — a vanilla-tonka fougère whose `orange blossom` is a white floral — collected
# freshness credit for it, outscoring Dior Homme Sport on a gym request. The expansion is a
# list of notes that should make a perfume a *candidate*; it is not a definition of the
# accord, and using it as one lets any note's secondary family widen what the customer asked
# for.
#
# Fresh is exactly LIGHT_FAMILIES, which is worth stating as an identity — those four
# families *are* what "منعش" describes, and the heaviness penalty already reads them that way.
REQUEST_FAMILIES = (
    (FRESH_REQUEST_TERMS, LIGHT_FAMILIES),
    (SWEET_REQUEST_TERMS, frozenset({"gourmand"})),
)


def request_families(term):
    """The families a requested term stands for.

    An accord word returns its stated definition; an ordinary ingredient returns whatever
    families that one note reads as, tolerantly — so "bergamot" still means citrus and an
    unmapped "elemi" still means nothing rather than something invented.
    """
    lowered = str(term or "").strip().lower()
    if not lowered:
        return frozenset()
    for triggers, accord in REQUEST_FAMILIES:
        if lowered in triggers:
            return accord
    return families((lowered,), tolerant=True)


def opposing_families(wanted):
    """The families that argue *against* a request for `wanted`.

    Asking for فريش is not only a request for citrus and mint — it is a statement about
    what the perfume should not mostly be. Stronger With You reaches the fresh expansion
    through `mint` and `lavender` while being vanilla, chestnut and amberwood at full
    weight, and with no counterweight it scored exactly what a genuine aquatic scored.

    Symmetric for the other direction: a مسكر request is answered less well by something
    predominantly citrus. A request that leans neither way — a bare "cedar", "pepper" —
    has nothing to oppose, and returns empty rather than inventing an opposite.
    """
    wanted = frozenset(wanted or ())
    light = len(wanted & LIGHT_FAMILIES)
    heavy = len(wanted & HEAVY_FAMILIES)
    if light > heavy:
        return HEAVY_FAMILIES
    if heavy > light:
        return LIGHT_FAMILIES
    return frozenset()
