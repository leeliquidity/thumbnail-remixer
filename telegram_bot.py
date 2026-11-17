#!/usr/bin/env python3
"""
Telegram front-end for the thumbnail remixer.

Send:   <youtube url> | <niche> | <optional your title>
Send a photo with a caption = your video title -> finds similar viral videos, builds thumbnails around your photo.
/similar <title>  -> same thing without a photo.
Send a photo with caption /face  -> saves your face; future remixes put you in them.
/noface  -> stop using face.   /find <query>  -> list viral outliers.
"""
import asyncio
import os
from pathlib import Path

from telegram import InputMediaPhoto, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import thumbnail_bot as tb

ALLOWED = {int(x) for x in os.getenv("TELEGRAM_ALLOWED_IDS", "").split(",") if x.strip()}
FACES = Path("faces"); FACES.mkdir(exist_ok=True)


def ok(u: Update) -> bool:
    return not ALLOWED or u.effective_user.id in ALLOWED


async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "📸 Photo + caption (your title) = find similar viral videos, build thumbnails around your photo\n"
        "/similar <title> = same, no photo\n"
        "Send: <youtube url> | <niche> | <optional title>\n"
        "Photo with caption /face = use your face.  /noface to stop.\n"
        "/find <search query> = viral outliers.")


async def face(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not ok(u) or not u.message.photo:
        return
    f = await u.message.photo[-1].get_file()
    await f.download_to_drive(FACES / f"{u.effective_user.id}.jpg")
    await u.message.reply_text("Face saved. Remixes will feature you.")


async def noface(u: Update, c: ContextTypes.DEFAULT_TYPE):
    (FACES / f"{u.effective_user.id}.jpg").unlink(missing_ok=True)
    await u.message.reply_text("Face removed.")


async def find(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not ok(u):
        return
    q = " ".join(c.args)
    if not q:
        return await u.message.reply_text("/find <query>")
    rows = await asyncio.to_thread(tb.find_outliers, q, 8)
    await u.message.reply_text("\n\n".join(
        f"{r['outlier']}x — {r['views']:,} views / {r['subs']:,} subs\n{r['title']}\n{r['url']}" for r in rows)
        or "Nothing found.", disable_web_page_preview=True)


async def _run_similar(u: Update, title: str, img_path: str | None):
    status = await u.message.reply_text("Searching similar videos…")
    loop = asyncio.get_running_loop()

    def log(m):
        asyncio.run_coroutine_threadsafe(status.edit_text(m), loop)

    try:
        s = await asyncio.to_thread(tb.similar, title, img_path, int(os.getenv("THUMBS_PER_RUN", "3")),
                                    6, 730, "high", None, 20000, log)
    except Exception as e:
        return await status.edit_text(f"Failed: {e}")
    refs = "\n".join(f"• {r['views']:,} views ({r['outlier']}x): {r['title'][:60]}" for r in s["references"])
    await u.message.reply_photo(open(s["reference_sheet"], "rb"),
                                caption=f"Studied:\n{refs}\n\nFormula: {s['viral_formula']}"[:1000])
    cap = ("Titles:\n" + "\n".join(f"• {t}" for t in s.get("title_options") or []) + "\n\n" +
           "\n".join(f"{i}. {c['name']}: \"{c['overlay_text']}\"" for i, c in enumerate(s["concepts"], 1)))[:1000]
    await u.message.reply_media_group([InputMediaPhoto(open(f, "rb"), caption=cap if i == 0 else None)
                                       for i, f in enumerate(s["thumbnails"])])
    await status.delete()


async def similar_photo(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not ok(u):
        return
    title = (u.message.caption or "").strip()
    if not title:
        return await u.message.reply_text("Add your video title as the photo caption.")
    f = await u.message.photo[-1].get_file()
    path = FACES / f"subject_{u.effective_user.id}_{u.message.message_id}.jpg"
    await f.download_to_drive(path)
    await _run_similar(u, title, str(path))


async def similar_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not ok(u):
        return
    title = " ".join(c.args)
    if not title:
        return await u.message.reply_text("/similar <your video title>")
    await _run_similar(u, title, None)


async def remix(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not ok(u):
        return
    parts = [p.strip() for p in u.message.text.split("|")]
    if len(parts) < 2:
        return await u.message.reply_text("Format: <url> | <niche> | <optional title>")
    url, niche, title = parts[0], parts[1], (parts[2] if len(parts) > 2 else None)
    fp = FACES / f"{u.effective_user.id}.jpg"
    status = await u.message.reply_text("On it…")
    loop = asyncio.get_running_loop()

    def log(m):
        asyncio.run_coroutine_threadsafe(status.edit_text(m), loop)

    try:
        s = await asyncio.to_thread(tb.remix, url, niche, title, str(fp) if fp.exists() else None,
                                    int(os.getenv("THUMBS_PER_RUN", "2")), "high", True, log)
    except Exception as e:
        return await status.edit_text(f"Failed: {e}")
    cap = (f"Hook: {s['hook']}\n\nTitle: {s['new_title']}\nAlt: " + " / ".join(s.get("title_options") or [])
           + f"\nOverlay: {s['overlay_text']}")[:1000]
    media = [InputMediaPhoto(open(f, "rb"), caption=cap if i == 0 else None) for i, f in enumerate(s["thumbnails"])]
    await u.message.reply_media_group(media)
    await status.delete()


def main():
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("noface", noface))
    app.add_handler(CommandHandler("find", find))
    app.add_handler(CommandHandler("similar", similar_cmd))
    app.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/face"), face))
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.CaptionRegex(r"^/face"), similar_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, remix))
    app.run_polling()


if __name__ == "__main__":
    main()
