"""Build offline production assets. Standard library only; never calls an API."""

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def write(relative, content):
    target = ROOT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main():
    data = json.loads((ROOT / "campaign.json").read_text(encoding="utf-8"))
    body = " ".join(cue["text"].replace("\n", " ") for cue in data["body_cues"])
    write("campaign-data.js", "// Generated from campaign.json by build_assets.py.\n"
          + "window.UGC_CAMPAIGN = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n")

    scripts = ["# Egyptian Arabic scripts and Google Flow prompts\n",
               "All versions target perfume-store owners. The shared body begins at 00:06 and the CTA at 00:25. "
               "Use your own voice for the body; these are recording scripts, not generated audio.\n",
               "## Shared narration: 00:06–00:32\n", body + "\n"]
    posts = ["# Facebook and Instagram captions\n",
             "Use the matching version after assembling and checking the actual video. "
             "Each caption directs viewers to the publishing page's inbox.\n"]
    voice = ["WEBVITAS AI — RECORDING SCRIPT\n",
             "Read only the Arabic. Timing labels are editing guides, not spoken words.\n",
             "COMMON BODY — starts at 00:06 in all three edits\n"]
    for cue in data["body_cues"]:
        voice.extend([f"[{cue['start']:05.1f}–{cue['end']:05.1f}]", cue["text"].replace("\n", " "), ""])
    voice.extend(["OPTIONAL HOOKS — record these only for the silent-presenter fallback", ""])

    for variant in data["variants"]:
        stem = f"{variant['id']}-{variant['slug']}"
        prompt = data["presenter_prompt"].replace("{{HOOK}}", variant["hook"])
        cues = variant["hook_cues"] + data["body_cues"]
        srt = "\n\n".join(
            f"{index}\n{timestamp(cue['start'])} --> {timestamp(cue['end'])}\n{cue['text']}"
            for index, cue in enumerate(cues, 1)
        ) + "\n"
        write(f"subtitles/{stem}.srt", srt)
        write(f"prompts/{stem}.txt", prompt + "\n")
        scripts.extend([f"## {variant['id']} — {variant['name']}\n",
                        "### Complete spoken script\n", variant["hook"] + " " + body + "\n",
                        "### Flow prompt\n", prompt + "\n"])
        posts.extend([f"## {variant['id']} — {variant['name']}\n", variant["post"] + "\n"])
        voice.extend([f"{variant['id']} — 00:00–00:06", variant["hook"], ""])

    scripts.extend(["## Silent presenter fallback\n", data["silent_prompt"] + "\n",
                    "Record the hook yourself. Show the silent presenter for two seconds, then the real demo "
                    "while your hook finishes. Keep the common body starting at 00:06.\n"])
    write("scripts-and-prompts.md", "\n".join(scripts))
    write("post-captions.md", "\n".join(posts))
    write("voiceover.txt", "\n".join(voice))
    write("prompts/silent-presenter.txt", data["silent_prompt"] + "\n")

    # Never erase collected results when refreshing creative assets.
    tracker = ROOT / "results.csv"
    if not tracker.exists():
        with tracker.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow([
                "variant", "platform", "window", "published_at_cairo", "post_url", "views",
                "reach", "average_watch_seconds", "unique_qualified_owner_conversations",
                "demos_booked", "qualified_conversations_per_1000_views", "demo_booking_rate",
                "notes",
            ])
            for variant in data["variants"]:
                for platform in ("Facebook", "Instagram"):
                    for window in ("24h", "7d"):
                        writer.writerow([variant["id"], platform, window] + [""] * 10)

    write("assets/end-card.svg", '''<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920" viewBox="0 0 1080 1920" role="img" aria-labelledby="title desc">
  <title id="title">WebVitas AI — ابعت كلمة ديمو</title>
  <desc id="desc">مساعد مبيعات متخصص في البرفانات. ابعت كلمة ديمو في رسالة وشوفه على منتجات محلك.</desc>
  <rect width="1080" height="1920" fill="#1c1a17"/>
  <path d="M100 210H980M100 1490H980" stroke="#4b4337" stroke-width="2"/>
  <g transform="translate(370 315) scale(1.35)">
    <path d="M1 2h11l10 27L31 2h10l9 27L59 2h11L55 43H45L36 19 27 43H16L1 2Z" fill="#fffefb"/>
    <path d="m31 2 14 41h10L41 2H31Z" fill="#d8be8a"/>
  </g>
  <text x="485" y="364" fill="#fffefb" font-family="Arial, sans-serif" font-weight="700" font-size="50">webvitas</text>
  <rect x="710" y="326" width="52" height="42" rx="8" fill="none" stroke="#d8be8a"/>
  <text x="736" y="355" fill="#d8be8a" text-anchor="middle" font-family="Arial, sans-serif" font-size="23">AI</text>
  <g font-family="Tahoma, Arial, sans-serif" text-anchor="middle" direction="rtl">
    <text x="540" y="530" fill="#d8be8a" font-size="42">لأصحاب محلات البرفانات</text>
    <text x="540" y="720" fill="#fffefb" font-size="88" font-weight="700">مساعد مبيعات</text>
    <text x="540" y="838" fill="#fffefb" font-size="82" font-weight="700">فاهم في البرفانات.</text>
    <text x="540" y="960" fill="#c4baaa" font-size="43">يرشّح · يجاوب · يسجّل الطلب</text>
    <rect x="145" y="1100" width="790" height="254" rx="28" fill="#d8be8a"/>
    <text x="540" y="1200" fill="#1c1a17" font-size="66" font-weight="700">ابعت كلمة ديمو</text>
    <text x="540" y="1280" fill="#1c1a17" font-size="34">في رسالة وشوفه على منتجات محلك</text>
  </g>
</svg>
''')
    print("Built scripts, prompts, 3 SRT files, page data and SVG. Existing tracker and PNG preserved.")


if __name__ == "__main__":
    main()
