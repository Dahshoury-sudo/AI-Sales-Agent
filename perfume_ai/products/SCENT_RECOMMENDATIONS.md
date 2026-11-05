# Scent label recommendations

Each perfume has a manually assigned `scent_tags` list. Existing products start
with no labels and become eligible for recommendations after you label them.
Product questions and orders can still use unlabeled products.

## Label the catalog

1. Sign in to the dashboard and open **إضافة منتجات جماعية** (`/api/import/`).
2. In **تصنيف روائح العطور الموجودة**, download your existing products.
3. Fill `scent_tags` with comma-separated labels, such as `fresh, sweet, light`.
   Keep `product_id` unchanged. The second sheet explains the allowed labels.
4. Upload the edited workbook using **حفظ تصنيفات العطور**.

The upload replaces the labels of existing products in your store. It does not
create products or change names, prices, stock, or notes. Blank cells keep the
current labels. A duplicate ID, unknown label, or product from another store
rejects the whole upload, with row-specific errors.

For individual changes, open **المنتجات**, edit a perfume, and select its
**طابع الريحة** checkboxes. Uncheck every label to clear its classification.
The new-product Excel template also accepts an optional `scent_tags` column,
immediately after `occasion`. Imports locate both columns by header name.

Allowed labels:

`fresh`, `sweet`, `oud`, `woody`, `citrus`, `floral`, `fruity`, `spicy`, `musky`,
`clean`, `powdery`, `smoky`, `leather`, `aquatic`, `light`, `heavy`.

## Customer scent wording

The extractor interprets the customer's latest answer in context and retains
these store conventions:

| Customer wording | Scent label |
| --- | --- |
| تبغي، تبغية، توباكو، tobacco | `smoky` |
| توابل، متبل | `spicy` |
| حمضي، حمضية | `citrus` |
| صابوني، صابونية، soapy | `clean` |
| فانيليا، فانيلا، vanilla as a scent style | `sweet` |

For example, **تبغي وحمضي** requires `smoky` and `citrus`.
**فريش ومش مسكر** requests `fresh` and excludes `sweet`.
A vanilla style does not require a recorded vanilla ingredient; an explicit
request such as **لازم يكون فيه فانيليا** does. The same distinction applies to
tobacco. Words inside a named reference perfume do not add scent or ingredient
requirements, and a recorded ingredient does not establish a product's labels.

Known wording corrects its own AI interpretation without discarding other
recognized phrases. Unfamiliar wording requires a quoted phrase from the latest
customer message. This evidence stays internal to extraction and merging; it is
not saved as a customer preference. Unrecognized wording is not guaranteed to
map to a label.

Later explicit preferences replace earlier styles; **كمان** adds to them. A
current exclusion removes the corresponding wanted style, while a later wanted
style can withdraw that exclusion. Budget-only answers retain saved preferences.
Explicit withdrawals and recipient resets retain their existing behavior.

For the current preference question, short choices such as **اول حاجة**,
**التانية**, and **آخر حاجة** select its stated options in order. This only
applies while that question is pending. **على ذوقك** delegates the scent choice
without choosing an occasion. **يا فريش يا خشبي** accepts either label;
**حاجة حلوة** means something nice and does not establish sweetness.

## How selection works

- Gender is established first. When it is missing, the agent asks exactly:
  **حضرتك محتاج برفان رجالي ولا حريمي**
  A stated choice or a saved choice for the same recipient skips this question.
  Explicit unisex is accepted, but a perfume name or "choose for me" does not
  establish gender. Other preferences are saved while waiting for the answer.
- “Fresh and sweet for work” requires both labels and a matching recorded occasion.
  “Fresh or sweet” accepts either label. Excluded labels remove a perfume.
- Occasion is optional. After gender is known, an occasion-only request gets a scent-style question.
  A scent preference alone, or an explicit “any scent,” is enough to search.
- Stock, gender, strict brand restrictions, requested size and bottle type,
  budget, literal ingredients, and explicit performance requirements still apply.
  Approximate budgets retain the existing 20% allowance; explicit ceilings and
  combined budgets remain strict.
- “Similar to X” uses X's assigned labels and excludes X itself. An unknown or
  unlabeled reference gets a style question. Labels do not establish close similarity.
- The AI sees every eligible product and normally selects one or two. There is
  no weighted scent ranking or cheapest-first candidate cutoff.
- Selected IDs, current eligibility, claims, and quotations are checked in code.
  An invalid answer gets one corrective retry, then a factual reply.
- An empty result asks which requirement can change. Requirements stay in place
  until the customer changes them or accepts a specific offered relaxation.

## Occasion categories

Use these six values in the product's `occasion` field:

| Occasion | Common customer wording |
| --- | --- |
| Daily & Casual | Everyday, university, errands, daytime, mosque/prayer |
| Work & Office | Work, meetings, job interviews |
| Night Out & Hanging Out | Friends, parties, clubbing, evenings out |
| Date Night & Romantic | Dates, romantic dinners |
| Formal & Special Occasions | Weddings, engagements, ceremonies |
| Beach, Summer & Gym | Beach, summer use, workouts, sports |

The last row is one category, including its comma. The extractor maps Arabic,
English and unfamiliar situations to the nearest category in context. A date
night does not also match nightlife. Occasion does not imply any scent labels.

Older catalog values such as `Office`, `Evening`, and `Sport/Casual` are recognized
when read; existing records do not need a migration. A perfume with multiple
recognized occasions can match any of those categories. Blank or unrecognized
catalog occasions cannot satisfy an occasion filter.

When the customer leaves the occasion to the agent, it keeps an existing stated
occasion or defaults to **Daily & Casual**. Choosing a scent, bottle size or one
of the offered perfumes does not change the occasion. Omitting an occasion
keeps the current optional behavior. An occasion the extractor cannot interpret
gets a clarification question; it does not silently broaden the search.

## Local experiment

Use the `huge` branch with the local `AIAgentHuge` database. Apply migrations with
`python manage.py migrate`; migration `0045_product_scent_tags` adds the field.
No classification is inferred from perfume names or notes.

Offline regressions: `python scripts/run_offline_tests.py --log artifacts/scent_labels/tests.log`.
Browser workflow: `python scripts/dashboard_ui/check_scent_labels_live.py`.
Both use disposable test databases. The browser check requires Python Playwright
and Microsoft Edge, or `SCENT_BROWSER_EXECUTABLE` pointing to another Chromium browser.
