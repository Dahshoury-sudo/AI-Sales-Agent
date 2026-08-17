"""Move the last two hardcoded per-store values into StoreSettings.

Both were global and therefore wrong for every store but the first:

  * bottle_image_url — was settings.BOTTLE_IMAGE_URL, one env var, so every
    store's customers were shown the first store's bottles and packaging.
  * business_facts — oil ratios, available bottle sizes, the ~90% match claim,
    and "we have a physical branch, come visit" were asserted in the shared
    system prompt as universal truths.

Seeded for the store the values were actually correct for, matched on the name
containing "perfamix" — the same store the InstaPay link in migration 0023
belonged to. Every other store is left blank on purpose: with business_facts
empty the bot makes no factual claims about ratios, sizes or a branch, and with
bottle_image_url empty it does not offer photos at all.
"""

from django.conf import settings
from django.db import migrations


LEGACY_BUSINESS_FACTS = """- كمية الزيت العطري: الزجاجة الـ 90 ملي تأخذ من 28 إلي 36 جرام من الزيت الخام، والأنواع الشتوية الثقيلة تأخذ نسبة أقل. وللأحجام الأصغر رد بنسبة وتناسب (مثلاً 50 ملي تأخذ 16 إلى 20 جرام).
- طبيعة المنتجات: كل العطور اللي بنبيعها "عطور تركيب" (مستوحاة من الماركات العالمية)، ونسبة التطابق مع العطور الأصلية توصل لحوالي 90% بسعر أوفر بكتير. لو العميل سأل "أصلي ولا تركيب؟" أكّد إن **العطر نفسه دايماً تركيب من عندنا**، بس متاح لبعض العطور "زجاجات أوريجينال" (بنفس شكل زجاجة البراند الأصلي). ❌ ممنوع تقول "عندنا عطور أوريجينال" — قول "متاح لبعض البرفانات زجاجات أوريجينال بس البرفان تركيب من عندنا".
- الأحجام المتاحة: زجاجات البراند (التركيب) متوفرة في حجمين بس، 50 ملي و 90 ملي. الزجاجات الأوريجينال بأحجام مختلفة حسب العطر (50، 60، 100، 150، وحتى 200 ملي لبعض العطور). لو سأل عن الأحجام بشكل عام اذكرله ده، ولو سأل عن عطر محدد اطلب اسمه عشان تتأكد من أحجامه.
- الستور على أرض الواقع: إحنا مش متجر إلكتروني بس، عندنا فرع وستور حقيقي. لو العميل طلب يجرب أو يشم العطور، رحّب بيه بشدة وقوله يشرفنا في الستور يجرب كل العطور قبل الشراء. 🔴 وبمجرد ما يبدي موافقته أو رغبته في الزيارة، اديله عنوان الستور التفصيلي فوراً من غير ما يسأل عنه."""


def seed_legacy_store_values(apps, schema_editor):
    StoreSettings = apps.get_model('products', 'StoreSettings')
    legacy = StoreSettings.objects.filter(store__name__icontains='perfamix')

    legacy.filter(business_facts='').update(business_facts=LEGACY_BUSINESS_FACTS)

    # The global env var was configured for this store; carry it over so the
    # bottle-photo feature keeps working without manual re-entry.
    global_image_url = getattr(settings, 'BOTTLE_IMAGE_URL', '') or ''
    if global_image_url:
        legacy.filter(bottle_image_url='').update(bottle_image_url=global_image_url)


def clear_legacy_store_values(apps, schema_editor):
    StoreSettings = apps.get_model('products', 'StoreSettings')
    StoreSettings.objects.filter(business_facts=LEGACY_BUSINESS_FACTS).update(business_facts='')

    global_image_url = getattr(settings, 'BOTTLE_IMAGE_URL', '') or ''
    if global_image_url:
        StoreSettings.objects.filter(bottle_image_url=global_image_url).update(bottle_image_url='')


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0024_storesettings_bottle_image_url_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_legacy_store_values, clear_legacy_store_values),
    ]
