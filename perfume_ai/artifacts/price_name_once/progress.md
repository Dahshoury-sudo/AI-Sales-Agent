# Price name once progress

- Approved plan and implementation requested.
- Current baseline HEAD c12314a; only unrelated ../.gitignore modified.
- Renderer and price reply composition delegated with disjoint ownership; root integrates and verifies.
- Renderer complete: one heading per product, first-seen product order, original per-product variant order. Focused renderer 16 tests and related 1,447 regressions passed; report in renderer-report.md.
- Composition implemented with product sections and exact quotation-row evidence; initial 24 focused/compact tests passed. Expanded adversarial and caller coverage in progress.
- Fresh independent read-only reviewer dispatched; root full suite and isolated live samples pending source stabilization.
- Independent review found four issues in mixed-detail routing, authenticity fallback, short-name boundaries, and recommendation composition validation. Fixes are implemented with targeted regressions; reviewer verified 48 tests.
- Initial root full run: 1,998 tests, four obsolete contiguous-text assertions. Updated assertions preserve exact prices, brand disclosures, recommendations, and name-count requirements.
- Follow-up root run: 2,003 tests, four failures (one fixture edit completed after import, plus three compatibility cases in the new narrow gate). Final compatibility fixes underway.
- First isolated live run: five scenarios / six turns, all quoted names once and exact stored prices, notes/performance and size follow-up retained; USD 0.007156960. Six evaluator flags arose from its earlier-bottle-label inference across redundant availability prose; actual records verified manually. Removing that redundant prose and rerunning samples before final report.
- Complete: final full suite passed 2,005 tests with zero unexpected HTTP (`full-tests-release.log`); final focused suite passed 233 tests. All accepted review findings and live wording regressions are covered, including formulation preservation and size-count wording.
- Live price/mixed/follow-up outputs verified for one name per perfume and exact catalogue facts. Final report: `report.md`; independent review: `final-review.md`.
