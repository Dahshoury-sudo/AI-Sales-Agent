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

For example, **تبغي وحمضي** requests `smoky` and `citrus`; a perfume sharing
either label is eligible, and matching both helps when the season fit is equal.
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
- “Fresh and sweet for work” accepts either label. This also applies to older
  saved requests with `scent_match="all"`; the customer's full preference list
  stays saved. Excluded labels still remove a perfume.
- Season and occasion are preferences, not eligibility filters. After a perfume
  shares at least one requested label, prefer a season match, then more shared
  labels, then a matching occasion. A fresh summer perfume ranks above a fresh
  and woody winter perfume for a fresh, woody, summer request. All assigned
  labels participate, including labels beyond the three used in descriptions.
- Missing season or occasion data earns no preference bonus. Existing season
  aliases and “All Seasons” still count as matches. Equal fits use current
  discussion, preferred brand, and previous offers. Contextual alternatives
  also retain the current ingredient affinity between equal fits; ingredient
  names do not supply scent labels. Product ID is the final deterministic tie-breaker.
- Occasion is optional. After gender is known, an occasion-only request gets a scent-style question.
  A scent preference alone, or an explicit “any scent,” is enough to search.
- Stock, gender, strict brand restrictions, requested size and bottle type,
  budget, perfume category, and literal ingredients still apply.
  Approximate budgets retain the existing 20% allowance; explicit ceilings and
  combined budgets remain strict.
- “Similar to X” uses X's assigned labels and excludes X itself. An unknown or
  unlabeled reference gets a style question, as does a reference whose labels
  have all been excluded without another wanted style. One permitted reference
  label is enough. Labels do not establish close similarity.
- The AI sees every eligible product and normally selects one or two. There is
  no cheapest-first candidate cutoff or ranking based on inferred ingredients.
  Performance is uniform across the catalog and does not distinguish choices.
- Selected IDs, current eligibility, nearest fit, claims, and quotations are
  checked in code. An invalid answer gets one corrective retry, then a factual
  reply using the same ordering. Combined budgets choose the nearest feasible
  set of the requested number of bottles, rather than simply the cheapest set.
  Contextual alternatives quote that exact affordable set of sizes and its
  total; unsupported model prices go through the existing retry and factual recovery.
- A partial match is recommended directly, without asking permission to relax
  season, occasion, or additional wanted labels. Descriptions use the perfume's
  actual labels and recorded occasion; differing preferences are not claimed
  as matches. Seasonal suitability claims are checked against each perfume's
  recorded season, including missing data. Contextual alternative suggestions
  use these same selection rules.
- Only a genuine lack of a shared scent style or an unmet firm requirement gets
  a clarification. A known size or budget blocker is explained as such. The
  agent does not automatically clear saved scent preferences or exclusions.

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
catalog occasions receive no occasion preference bonus.

When the customer leaves the occasion to the agent, it keeps an existing stated
occasion or defaults to **Daily & Casual**. Choosing a scent, bottle size or one
of the offered perfumes does not change the occasion. Omitting an occasion
keeps the current optional behavior. An occasion the extractor cannot interpret
does not block a recommendation with a known scent preference. It earns no
occasion bonus and cannot support a claim of suitability.

## Local experiment

Use the `huge` branch with the local `AIAgentHuge` database. Apply migrations with
`python manage.py migrate`; migration `0045_product_scent_tags` adds the field.
No classification is inferred from perfume names or notes.

Offline regressions: `python scripts/run_offline_tests.py --log artifacts/scent_labels/tests.log`.
Browser workflow: `python scripts/dashboard_ui/check_scent_labels_live.py`.
Both use disposable test databases. The browser check requires Python Playwright
and Microsoft Edge, or `SCENT_BROWSER_EXECUTABLE` pointing to another Chromium browser.
