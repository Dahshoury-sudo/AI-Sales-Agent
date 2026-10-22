# WebVitas AI — Egyptian UGC production pack

Open **[index.html](index.html)** in Chrome or Edge. It contains the three ad variants, a silent timed storyboard, copyable Flow prompts, scripts, subtitles and post captions. The preview explains the shots; it is not finished ad footage or a recording of the agent.

## Ready here / remaining production

Ready: three Egyptian Arabic scripts, three presenter prompts plus a silent fallback, draft SRT subtitles, a branded 1080 × 1920 end card, recording instructions, publishing copy, a lead-reply script, and a blank results tracker.

Still needed: presenter clips generated in your own Google Flow account, your voice recording, actual agent demo footage, final assembly, and publication. No video-generation account or social connector is available in this workspace. No presenter footage, speech or finished ad has been generated; nothing has been published. No new subscription or API purchase is part of this pack.

The brand comes from the existing WebVitas AI dashboard. The audience is Egyptian perfume-store owners. Every version directs viewers to message the publishing page with **ديمو**. This does not require a signup page or a new website.

## 1. Record the agent once

Use an existing test store and a test session. Do not run this sequence in a store that could dispatch a real order or alert actual customers. If no test store is available, capture an existing consented, redacted conversation and its saved order instead. The pack does not create store data or call the live AI API. Fresh agent conversations use the SaaS's normal inference allowance; reuse a suitable existing recording if zero additional usage is essential.

1. In the dashboard, open **شات تجريبي** (the existing `/api/demo/` view). Do not display the settings screen or API keys. Hide desktop notifications and unrelated tabs. Use a narrow browser window and enlarge text until the important reply is readable in a 1080 × 1920 crop.
2. Pick a real scent preference and budget that the test catalog can satisfy, with a cheaper appropriate alternative. The example below assumes such options exist around 500 EGP; adjust that customer budget to the catalog before recording. Do not change prices or fabricate replies to fit the script.
3. Send these **customer messages**, responding naturally to any clarification the agent needs:
   - `عايز برفان رجالي هادي للشغل، مش مسكر قوي، وفي حدود ٥٠٠ جنيه.`
   - Once the agent recommends an actual product: `السعر عالي عليا شوية. فيه بديل أوفر وينفع للشغل؟`
   - After it offers an actual alternative: `هاخد ده. إيه الأحجام المتاحة؟`
   - Choose a size actually offered, then say `عايز أطلبه.`
   - Supply only the test store's designated test identity, test phone and test address. Do not put someone else's plausible phone number in the demo.
   - After the agent shows the correct order, shipping and total, reply `تمام، أكد الطلب.`
4. Verify the test order appears in the dashboard with the correct product, size, quantity and total. Capture that result. If this does not succeed, fix or select another genuine successful run before using the order-completion claim.
5. Capture with Windows Snipping Tool screen recording or your existing free recorder. Keep the original recording. Cut waiting and typing time, but preserve the actual questions, replies and confirmation in order. Add the small caption `مقتطفات من محادثة تجريبية` during the edited demo; do not imply the jump-cut duration is real response latency.
6. Keep personal details and identifiers outside the crop or cover them with solid rectangles. Record enough hold time on each reply to make it readable. Never use the mocked dashboard UI-check fixtures as evidence of agent performance.

Required shots: recommendation with explanation; price objection and available alternative; order summary and explicit confirmation; saved test order. If a reply cannot be read in the proposed slot, extend that shot and retime the narration and captions. Thirty-two seconds is an editing target, not a speed claim.

## 2. Generate only the presenter hooks

Open [Google Flow](https://labs.google/fx/tools/flow) using your Google AI Pro account. Read the included balance and generation cost shown there. Use a vertical, audio-capable model available in your plan; start with Veo Fast if offered. Generate one output at a time. Do not buy credits or use the API for this workflow.

Paste the selected prompt from [scripts-and-prompts.md](scripts-and-prompts.md). Generate A first, and retain a clean frame of its fictional presenter as the reference for B and C where the tool supports image references. Keep the same framing, clothing, lighting and background.

Listen for Egyptian delivery, exact wording, natural pace and lip-sync. Check face and hand artifacts at normal speed. Allow at most **three attempts per hook**, and stop sooner if you reach a **200-credit total cap** or exhaust your included balance. A failed-but-refunded request does not count toward the spend cap; an unappealing successful output does.

If speech still sounds wrong, use the supplied silent-presenter prompt and record all three hooks yourself. Play the silent shot for two seconds, then cut to the real demo while your hook finishes. Keep the common body starting at 00:06. Do not put mismatched speech over a visibly talking face.

If six seconds is unavailable, generate the shortest suitable length, keeping the spoken line within the first six seconds, then trim quiet time. Never speed up speech unnaturally to hit a duration. If included generation is unavailable or exhausted, reuse a suitable existing shot or record your own opening; no paid fallback is authorized by this pack.

## 3. Record your voice and assemble

Read [voiceover.txt](voiceover.txt) on a phone in a quiet, softly furnished room. Record the shared body and CTA once, with short pauses between sentences. Keep the phone about 15–20 cm away and record two takes. Record the optional hooks only if using the silent-presenter fallback. Use your normal Egyptian delivery; no voice cloning or paid voice tool is needed.

In CapCut, create a **1080 × 1920, 30 fps** project using only free functions and assets. Basic cuts, scale/crop, voice recording, manual text, and the provided PNG are sufficient. If your installed edition gates a feature, use manual captions and a different basic control; do not start a paid trial.

| Time | Picture | Audio |
| --- | --- | --- |
| 00:00–00:06 | Presenter A, B or C | Generated hook; or your voice for the silent fallback |
| 00:06–00:15.5 | Real preference/budget and recommendation | Shared narration |
| 00:15.5–00:19 | Real objection and alternative | Shared narration |
| 00:19–00:25 | Summary → confirmation → saved order | Shared narration |
| 00:25–00:32 | `assets/end-card.png` | Shared demo invitation |

Mute the screen recording. Start the shared voiceover at 00:06. Use straight cuts with no music for the first test, so there are no music-license or subscription dependencies. Reuse the same body, voice and end card in all three exports.

Import the matching `subtitles/*.srt` if supported, otherwise add the same text manually. **SRT timings are draft guides**, and must be aligned to the actual voice recording. The captions are slightly condensed for reading; they are not a verbatim transcript. Use a legible Arabic font, bold white text on a dark panel, at most two lines. At 1080 × 1920 start around 52–60 px; leave at least 100 px on the left, 180 px on the right, 240 px at the top and 420 px at the bottom. Placement varies by app: inspect each platform's preview. Keep the demonstration's key reply above the subtitle panel. Hide the extra subtitle panel during the end card if it clashes with the built-in CTA.

The supplied end card intentionally contains no phone number, URL, subscription price, invented testimonial or promised sales result. It sends viewers to the page's own inbox.

Export H.264 MP4 at 1080 × 1920, 30 fps; use approximately 8–12 Mbps where adjustable. Suggested names: `webvitas-eg-A.mp4`, `webvitas-eg-B.mp4`, `webvitas-eg-C.mp4`. Watch each export on a phone with sound on and off, confirming Arabic letter order, caption timing and readable demo text.

## 4. Publish and track

Use the matching copy in [post-captions.md](post-captions.md). Upload A, B and C organically on three separate days, at the same local time on each platform. Default schedule: A on day 1, B on day 3, C on day 5, all at 19:00 Cairo time. This is a controlled starting schedule, not a claim about an optimal posting time. Keep the same offer and body. Paid distribution is separate and is not started here.

Publish to the Facebook Page and Instagram account where interested owners can actually send you a message. Enable the platform's applicable AI-content disclosure for the generated presenter. The caption also identifies it as generated. Preview the placement before publication.

Use [results.csv](results.csv): one row per variant, platform and measurement window. Record metrics 24 hours and 7 days after each post. The windows are cumulative: do not add 24-hour and 7-day figures together. Leave unavailable metrics blank. Compare variants **within each platform**; do not pool Facebook and Instagram view or watch-time definitions.

Primary outcomes: unique qualified store-owner conversations and demos booked. A qualified conversation means the person owns or manages a perfume store and expresses interest in trying the agent. Deduplicate repeat contacts across posts in your normal inbox or lead system. Attribute a DM to a variant only if the platform shows the referring post or the person identifies it; mark unknown attribution in a separate tracker row rather than guessing.

If supported, calculate `qualified conversations / views × 1000` per platform as a descriptive rate, and `demos booked / qualified conversations` as the demo booking rate. Leave rates blank when the denominator is zero. Sparse organic data is directional; use owner questions to revise the next hook and do not treat a small numerical lead as proof of paid-ad performance.

Suggested manual reply to a demo request:

> أهلاً بيك! اسم محلك إيه، وبتستقبل طلبات أكتر على فيسبوك ولا إنستجرام ولا واتساب؟ ابعتلي لينك الصفحة، ونرتب ديمو يوضح الترشيح والتعامل مع السعر وتسجيل الطلب.

Then ask for a small sample of the store's product information if needed for a tailored demo. Do not ask for account passwords or access tokens in DMs. These are response templates; this pack does not send messages.

## Tool choice and maintenance

Use ChatGPT Go for copy refinement and Google Flow for video generation under your existing allowance. This workflow does not depend on ChatGPT video access. The prior research found Google AI Pro included Flow credits and Egypt in Flow's supported regions; verify the actual balance in your account because entitlements and per-generation costs change.

- [Google Flow credit details](https://support.google.com/flow/answer/16526234?hl=en)
- [Google Flow access and watermark details](https://support.google.com/flow/answer/16353333?hl=en)
- [Google Flow supported regions](https://support.google.com/flow/answer/16353544?hl=en)
- [Higgsfield's UGC workflow](https://higgsfield.ai/blog/how-to-make-ai-ugc-videos)

Reconsider Higgsfield only after a concrete bottleneck appears: repeated presenter-consistency failures or enough variant production that the saved time justifies a subscription. Egyptian Arabic quality would still need a sample test. No purchase is needed to use the current pack.

The source copy is [campaign.json](campaign.json). To regenerate text assets and the embedded page data after changing it, run `python marketing/egypt-ugc/build_assets.py` from the repository root. This refreshes generated text and SVG files but leaves an existing results CSV and the PNG untouched. Re-export the PNG from the SVG if the graphic changes. No application code or database migration is required.
