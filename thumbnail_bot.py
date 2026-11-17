#!/usr/bin/env python3
"""
Viral Thumbnail Remixer
-----------------------
Give it any YouTube video (or let it find outliers in a niche) + your niche.
It pulls the thumbnail, reverse-engineers WHY it works (layout, hook, palette,
text, emotion), then generates fresh 1280x720 thumbnails for your niche that
use the same proven structure — new subject, new text, no copied people/logos.

Usage:
  python thumbnail_bot.py remix "https://youtu.be/VIDEO_ID" --niche "NQ futures trading"
  python thumbnail_bot.py remix URL --niche "credit repair" --title "How I fixed my 520 score" --face me.jpg -n 3
  python thumbnail_bot.py similar "How I passed my prop firm challenge" --image me.jpg -n 3
  python thumbnail_bot.py find "make money online" --top 5            # list viral outliers
  python thumbnail_bot.py find "make money online" --top 3 --remix-niche "trading"   # find + remix all
"""
import argparse
import base64
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ANALYZE_MODEL = os.getenv("ANALYZE_MODEL", "claude-sonnet-5-5")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1")
OUT_DIR = Path(os.getenv("OUT_DIR", "out"))

# ---------------------------------------------------------------- YouTube ----

VID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")


def video_id(s: str) -> str:
    s = s.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = VID_RE.search(s)
    if not m:
        raise ValueError(f"Can't find a YouTube video ID in: {s}")
    return m.group(1)


def fetch_thumbnail(vid: str) -> bytes:
    for name in ("maxresdefault", "sddefault", "hqdefault"):
        r = requests.get(f"https://i.ytimg.com/vi/{vid}/{name}.jpg", timeout=20)
        if r.ok and len(r.content) > 2000:
            return r.content
    raise RuntimeError(f"No thumbnail found for {vid}")


def fetch_meta(vid: str) -> dict:
    try:
        r = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
            timeout=15,
        )
        r.raise_for_status()
        d = r.json()
        return {"title": d.get("title", ""), "channel": d.get("author_name", "")}
    except Exception:
        return {"title": "", "channel": ""}


def _yt_search(query: str, order: str, days: int | None, pool: int = 50, duration: str | None = None) -> list[dict]:
    key = os.getenv("YOUTUBE_API_KEY")
    if not key:
        sys.exit("This needs YOUTUBE_API_KEY in .env (free: Google Cloud console -> YouTube Data API v3)")
    api = "https://www.googleapis.com/youtube/v3"
    params = {"part": "snippet", "q": query, "type": "video", "order": order,
              "maxResults": min(pool, 50), "key": key}
    if days:
        params["publishedAfter"] = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if duration:
        params["videoDuration"] = duration
    s = requests.get(f"{api}/search", params=params, timeout=20).json()
    if "error" in s:
        raise RuntimeError(s["error"].get("message"))
    ids = [i["id"]["videoId"] for i in s.get("items", [])]
    if not ids:
        return []
    v = requests.get(f"{api}/videos", params={
        "part": "statistics,snippet", "id": ",".join(ids), "key": key}, timeout=20).json()
    vids = v.get("items", [])
    ch_ids = list({x["snippet"]["channelId"] for x in vids})
    subs = {}
    for i in range(0, len(ch_ids), 50):
        c = requests.get(f"{api}/channels", params={
            "part": "statistics", "id": ",".join(ch_ids[i:i + 50]), "key": key}, timeout=20).json()
        for ch in c.get("items", []):
            subs[ch["id"]] = int(ch["statistics"].get("subscriberCount", 0) or 0)
    rows = []
    for x in vids:
        views = int(x["statistics"].get("viewCount", 0))
        sc = max(subs.get(x["snippet"]["channelId"], 0), 1000)
        rows.append({
            "id": x["id"], "url": f"https://youtu.be/{x['id']}",
            "title": x["snippet"]["title"], "channel": x["snippet"]["channelTitle"],
            "views": views, "subs": sc, "outlier": round(views / sc, 1),
        })
    return rows


def find_outliers(query: str, top: int = 5, days: int = 90, pool: int = 50) -> list[dict]:
    """Find videos massively outperforming their channel size (views / subs)."""
    rows = _yt_search(query, "viewCount", days, pool, "medium")
    rows.sort(key=lambda r: r["outlier"], reverse=True)
    return rows[:top]


def find_similar(title: str, top: int = 6, days: int | None = 730, min_views: int = 20000) -> list[dict]:
    """Videos similar to a title, ranked by virality: blends outlier ratio with raw views."""
    import math
    seen, rows = set(), []
    for order in ("relevance", "viewCount"):
        for r in _yt_search(title, order, days):
            if r["id"] not in seen and r["views"] >= min_views:
                seen.add(r["id"]); rows.append(r)
    for r in rows:
        r["score"] = round(math.log10(r["views"] + 1) * (1 + math.log10(max(r["outlier"], 0.1) + 1)), 2)
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:top]


# ------------------------------------------------------------------- LLM ----

ANALYZE_PROMPT = """You are a YouTube packaging strategist who has studied thousands of viral thumbnails.
Reverse-engineer this thumbnail. Video title: "{title}" (channel: {channel}).

Return ONLY JSON:
{{
  "hook_mechanism": "the single psychological trigger (curiosity gap, contrast, shock, before/after, big number, forbidden knowledge, etc.)",
  "why_it_works": "2-3 sentences",
  "composition": {{"layout": "", "subject_position": "", "subject_scale": "% of frame", "focal_point": "", "negative_space": ""}},
  "subject": {{"type": "person/object/scene", "expression": "", "pose_gesture": "", "lighting": ""}},
  "text_overlay": {{"words": "", "word_count": 0, "font_style": "", "color": "", "outline_or_shadow": "", "position": "", "size": "% of frame height"}},
  "color_palette": ["#hex", "..."],
  "contrast_strategy": "",
  "background": "",
  "graphic_elements": ["arrows, circles, emojis, logos, charts, blur, glow, etc."],
  "emotion": "",
  "title_thumbnail_relationship": "how thumbnail and title work together (thumbnail should NOT repeat title)"
}}"""

REMIX_PROMPT = """You are a YouTube packaging strategist. Below is a breakdown of a proven viral thumbnail.
Remix it for a NEW niche, keeping the exact psychological structure that made it work.

ORIGINAL TITLE: "{orig_title}"
BREAKDOWN:
{analysis}

NEW NICHE: {niche}
{topic_line}
{face_line}

Rules:
- Keep: layout, subject placement & scale, hook mechanism, palette/contrast strategy, text size/position/style, graphic element types, emotion.
- Swap: all subject matter, props, and text so they fit the new niche authentically.
- Overlay text max 4 words, must create a curiosity gap WITH the title, never repeat it.
- No real celebrities, no brand logos, nothing copied from the original's people.
- image_prompt must be a single, highly detailed prompt for an image model producing a 16:9 YouTube thumbnail: describe subject, expression, pose, lighting, background, colors (with hex), every graphic element, and the exact overlay text in quotes with font style/color/outline/position. Ultra-sharp, high-contrast, bold, mobile-readable.

Return ONLY JSON:
{{
  "title": "new video title for the niche",
  "title_options": ["", "", ""],
  "overlay_text": "",
  "concept": "one sentence",
  "image_prompt": "",
  "prompt_variations": ["alt full image_prompt #2", "alt full image_prompt #3"]
}}"""


SYNTH_PROMPT = """You are a YouTube packaging strategist. The first {k} images are thumbnails from the
top-performing videos similar to the creator's video. {creator_clause}

Reference videos (in image order):
{refs}

Creator's video title: "{title}"
{extra}

1. Find the shared viral formula across the references: what repeats in the high performers
   (hook type, layout, subject scale, expression, text length/style, palette, contrast, graphic devices).
2. Describe the creator's image and how to use it best (crop, cutout, scale, placement, expression it shows).
3. Write {n} distinct thumbnail concepts that put the creator's image into that formula. Each one borrows its
   layout from one specific reference (give its 1-based index). Overlay text is max 4 words, creates a
   curiosity gap with the title and never repeats it. No celebrities or brand logos from the references.
   image_prompt: one detailed prompt for a 16:9 thumbnail covering subject (from the creator's image), expression,
   pose, lighting, background, hex colors, every graphic element, and the exact overlay text in quotes with
   font, color, outline and position.

Return ONLY JSON:
{{
  "viral_formula": {{"hook": "", "layout": "", "subject": "", "text": "", "palette": ["#hex"], "graphics": [""], "summary": "2-3 sentences"}},
  "per_reference": [{{"index": 1, "why_it_works": "one sentence"}}],
  "creator_image": {{"description": "", "how_to_use": ""}},
  "title_options": ["improved version of the creator's title", "", ""],
  "concepts": [{{"name": "", "layout_from": 1, "overlay_text": "", "image_prompt": ""}}]
}}"""


def _img_block(b: bytes) -> dict:
    im = Image.open(io.BytesIO(b)).convert("RGB")
    im.thumbnail((1280, 1280))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


def synthesize(ref_imgs: list[bytes], refs: list[dict], user_img: bytes | None, title: str,
               n: int, notes: str | None) -> dict:
    blocks = [_img_block(b) for b in ref_imgs]
    if user_img:
        blocks.append(_img_block(user_img))
    ref_txt = "\n".join(f"{i}. \"{r['title']}\" ({r['channel']}): {r['views']:,} views, {r['outlier']}x channel subs"
                        for i, r in enumerate(refs, 1))
    clause = ("The LAST image is the creator's own image that the new thumbnail MUST be built around "
              "(keep it recognizable: same person/product/object).") if user_img else \
             "No creator image was provided, so invent the best subject. Wherever this says 'creator's image', use that subject."
    prompt = SYNTH_PROMPT.format(k=len(ref_imgs), refs=ref_txt, title=title, extra=notes or "", n=n,
                                 creator_clause=clause)
    blocks.append({"type": "text", "text": prompt})
    return _claude(blocks, max_tokens=5000)


def contact_sheet(imgs: list[bytes], path: Path):
    tiles = [Image.open(io.BytesIO(b)).convert("RGB").resize((480, 270)) for b in imgs]
    cols = 3; rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 480, rows * 270), "black")
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * 480, (i // cols) * 270))
    sheet.save(path, quality=90)


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"Model didn't return JSON:\n{text[:500]}")
    return json.loads(m.group(0))


def _claude(content: list, max_tokens: int = 3000) -> dict:
    from anthropic import Anthropic
    msg = Anthropic().messages.create(
        model=ANALYZE_MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
    )
    return _json("".join(b.text for b in msg.content if b.type == "text"))


def analyze(img: bytes, meta: dict) -> dict:
    return _claude([
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.b64encode(img).decode()}},
        {"type": "text", "text": ANALYZE_PROMPT.format(**meta)},
    ])


def remix_plan(analysis: dict, orig_title: str, niche: str, topic: str | None, has_face: bool) -> dict:
    return _claude([{"type": "text", "text": REMIX_PROMPT.format(
        orig_title=orig_title, analysis=json.dumps(analysis, indent=2), niche=niche,
        topic_line=f"NEW VIDEO TOPIC/TITLE: {topic}" if topic else "Invent a strong video topic for this niche.",
        face_line=("The creator's face photo is provided; the main person in the thumbnail must be THIS person "
                   "(same face/identity), with the expression and pose from the breakdown.") if has_face else "",
    )}])

# ---------------------------------------------------------------- Images ----


def to_thumb(png_bytes: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = im.size
    tw, th = (w, int(w * 9 / 16)) if w / h < 16 / 9 else (int(h * 16 / 9), h)
    left, top = (w - tw) // 2, (h - th) // 2
    return im.crop((left, top, left + tw, top + th)).resize((1280, 720), Image.LANCZOS)


def generate(prompt: str, ref: bytes | None = None, face: bytes | None = None, quality: str = "high",
             subject: bytes | None = None) -> bytes:
    """ref = layout/style reference, face = creator identity, subject = the creator image to build around."""
    from openai import OpenAI
    client = OpenAI()
    files, guide = [], []
    if subject:
        files.append(("subject.jpg", subject, "image/jpeg"))
        guide.append(f"Image {len(files)} is the CREATOR'S IMAGE: the thumbnail is built around it. Keep its main "
                     "subject (person, product or object) clearly recognizable and faithful. You may cut it out, "
                     "re-light it, scale it and reposition it.")
    if ref:
        files.append(("reference.jpg", ref, "image/jpeg"))
        guide.append(f"Image {len(files)} is a LAYOUT/STYLE REFERENCE ONLY: match its composition, framing, "
                     "subject scale, text placement, contrast and energy. Do NOT reproduce its people, faces, "
                     "logos, objects or text.")
    if face:
        files.append(("face.jpg", face, "image/jpeg"))
        guide.append(f"Image {len(files)} is the creator: the main person must have this exact face and identity.")
    full = (" ".join(guide) + "\n\n" + prompt) if guide else prompt
    full += "\n\nOutput: 16:9 YouTube thumbnail, crisp, high contrast, text perfectly spelled and legible on mobile."
    if files:
        r = client.images.edit(model=IMAGE_MODEL, image=files, prompt=full, size="1536x1024",
                               quality=quality, input_fidelity="high")
    else:
        r = client.images.generate(model=IMAGE_MODEL, prompt=full, size="1536x1024", quality=quality)
    return base64.b64decode(r.data[0].b64_json)


def _to_jpeg(b: bytes) -> bytes:
    im = Image.open(io.BytesIO(b)).convert("RGB")
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=95)
    return buf.getvalue()


# -------------------------------------------------------------- Pipeline ----


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def remix(url: str, niche: str, title: str | None = None, face_path: str | None = None,
          n: int = 2, quality: str = "high", use_ref: bool = True, log=print) -> dict:
    vid = video_id(url)
    out = OUT_DIR / f"{vid}_{slug(niche)}_{datetime.now():%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)

    log(f"[1/4] Pulling thumbnail {vid}")
    img, meta = fetch_thumbnail(vid), fetch_meta(vid)
    (out / "original.jpg").write_bytes(img)

    log(f"[2/4] Breaking down: {meta['title'] or vid}")
    analysis = analyze(img, meta)
    (out / "analysis.json").write_text(json.dumps(analysis, indent=2))

    face = Path(face_path).read_bytes() if face_path else None
    log(f"[3/4] Remixing for niche: {niche}")
    plan = remix_plan(analysis, meta["title"], niche, title, bool(face))
    (out / "plan.json").write_text(json.dumps(plan, indent=2))

    prompts = [plan["image_prompt"], *plan.get("prompt_variations", [])][:max(n, 1)]
    while len(prompts) < n:
        prompts.append(plan["image_prompt"])
    files = []
    for i, p in enumerate(prompts, 1):
        log(f"[4/4] Generating thumbnail {i}/{n}")
        thumb = to_thumb(generate(p, img if use_ref else None, face, quality))
        f = out / f"thumb_{i}.jpg"
        thumb.save(f, quality=95)
        files.append(str(f))

    summary = {"source": f"https://youtu.be/{vid}", "source_title": meta["title"],
               "hook": analysis.get("hook_mechanism"), "why": analysis.get("why_it_works"),
               "new_title": plan.get("title"), "title_options": plan.get("title_options"),
               "overlay_text": plan.get("overlay_text"), "thumbnails": files, "folder": str(out)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def similar(title: str, image_path: str | None = None, n: int = 3, refs_n: int = 6,
            days: int | None = 730, quality: str = "high", notes: str | None = None,
            min_views: int = 20000, log=print) -> dict:
    out = OUT_DIR / f"similar_{slug(title)}_{datetime.now():%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)

    log(f"[1/4] Searching for videos like: {title}")
    refs = find_similar(title, refs_n, days, min_views)
    if not refs:
        raise RuntimeError("No similar videos found. Try a broader title, or lower --min-views or raise --days.")
    ref_imgs = []
    for r in refs:
        ref_imgs.append(fetch_thumbnail(r["id"]))
    (out / "refs").mkdir(exist_ok=True)
    for i, (r, b) in enumerate(zip(refs, ref_imgs), 1):
        (out / "refs" / f"{i}_{r['id']}.jpg").write_bytes(b)
    contact_sheet(ref_imgs, out / "references.jpg")
    (out / "references.json").write_text(json.dumps(refs, indent=2))

    user_img = _to_jpeg(Path(image_path).read_bytes()) if image_path else None
    if user_img:
        (out / "your_image.jpg").write_bytes(user_img)

    log(f"[2/4] Finding the shared viral formula across {len(refs)} thumbnails")
    plan = synthesize(ref_imgs, refs, user_img, title, n, notes)
    (out / "plan.json").write_text(json.dumps(plan, indent=2))

    log("[3/4] Building thumbnails around your image")
    files = []
    concepts = plan.get("concepts", [])[:n]
    for i, c in enumerate(concepts, 1):
        log(f"[4/4] Generating {i}/{len(concepts)}: {c.get('name', '')}")
        idx = int(c.get("layout_from", 1) or 1)
        ref = ref_imgs[idx - 1] if 1 <= idx <= len(ref_imgs) else ref_imgs[0]
        thumb = to_thumb(generate(c["image_prompt"], ref=ref, subject=user_img, quality=quality))
        f = out / f"thumb_{i}_{slug(c.get('name', 'concept'))}.jpg"
        thumb.save(f, quality=95)
        files.append(str(f))

    summary = {
        "title": title,
        "references": [{"url": r["url"], "title": r["title"], "views": r["views"], "outlier": r["outlier"]} for r in refs],
        "viral_formula": plan.get("viral_formula", {}).get("summary"),
        "title_options": plan.get("title_options"),
        "concepts": [{"name": c.get("name"), "overlay_text": c.get("overlay_text"),
                      "layout_from": refs[int(c.get("layout_from", 1) or 1) - 1]["url"]
                      if 1 <= int(c.get("layout_from", 1) or 1) <= len(refs) else None} for c in concepts],
        "thumbnails": files, "reference_sheet": str(out / "references.jpg"), "folder": str(out),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main():
    ap = argparse.ArgumentParser(description="Recreate viral YouTube thumbnails for any niche")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("remix", help="Remix one video's thumbnail")
    r.add_argument("url")
    r.add_argument("--niche", required=True)
    r.add_argument("--title", help="Your video's title/topic (optional — it'll invent one)")
    r.add_argument("--face", help="Path to your face photo to put yourself in it")
    r.add_argument("-n", type=int, default=2, help="Number of thumbnails (default 2)")
    r.add_argument("--quality", default="high", choices=["low", "medium", "high"])
    r.add_argument("--no-ref", action="store_true", help="Don't feed original as visual reference (text-only)")

    f = sub.add_parser("find", help="Find viral outliers (views >> subs) for a search query")
    f.add_argument("query")
    f.add_argument("--top", type=int, default=5)
    f.add_argument("--days", type=int, default=90)
    f.add_argument("--remix-niche", help="Also remix every result for this niche")
    f.add_argument("-n", type=int, default=1)
    f.add_argument("--face")

    sm = sub.add_parser("similar", help="Title + your image -> find similar viral videos, build thumbnails around your image")
    sm.add_argument("title", help="Your video title")
    sm.add_argument("--image", "-i", help="Your image (face, product, screenshot...) to build around")
    sm.add_argument("-n", type=int, default=3, help="Thumbnails to make (default 3)")
    sm.add_argument("--refs", type=int, default=6, help="Similar videos to study (default 6, max ~10)")
    sm.add_argument("--days", type=int, default=730, help="Only videos from the last N days (0 = any time)")
    sm.add_argument("--min-views", type=int, default=20000)
    sm.add_argument("--notes", help="Extra direction, e.g. 'show a green P&L, keep it clean'")
    sm.add_argument("--quality", default="high", choices=["low", "medium", "high"])

    a = ap.parse_args()
    if a.cmd == "similar":
        s = similar(a.title, a.image, a.n, a.refs, a.days or None, a.quality, a.notes, a.min_views)
        print(json.dumps(s, indent=2))
    elif a.cmd == "remix":
        s = remix(a.url, a.niche, a.title, a.face, a.n, a.quality, not a.no_ref)
        print(json.dumps(s, indent=2))
    else:
        rows = find_outliers(a.query, a.top, a.days)
        for x in rows:
            print(f"{x['outlier']:>7}x  {x['views']:>11,} views  {x['subs']:>9,} subs  {x['url']}  {x['title']}")
        if a.remix_niche:
            for x in rows:
                s = remix(x["url"], a.remix_niche, None, a.face, a.n)
                print(json.dumps(s, indent=2))


if __name__ == "__main__":
    main()
