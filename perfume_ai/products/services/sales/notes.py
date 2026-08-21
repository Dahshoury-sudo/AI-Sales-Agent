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


def families(notes):
    """The accord families a set of notes reads as.

    Unknown notes contribute nothing rather than a catch-all family: an unrecognised
    string is genuinely unknown, and bucketing it would invent similarity.
    """
    found = set()
    for note in notes:
        found.update(NOTE_FAMILIES.get(note, ()))
    return frozenset(found)


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
