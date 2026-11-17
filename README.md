# Viral Thumbnail Remixer

Takes any viral YouTube thumbnail → breaks down why it works → recreates that structure for your niche.

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY + OPENAI_API_KEY
```

## Use
```bash
# ⭐ Your title + your image → studies the most viral similar videos → builds thumbnails around your image
python thumbnail_bot.py similar "How I passed my prop firm challenge" --image me.jpg -n 3
python thumbnail_bot.py similar "I tried day trading for 30 days" -i pnl_screenshot.png --notes "keep it clean, green money vibe"
#   --refs 8 (videos to study)  --days 365 (recency)  --min-views 50000

# Remix one thumbnail
python thumbnail_bot.py remix "https://youtu.be/VIDEO_ID" --niche "NQ futures trading"

# With your own title + your face in it, 3 versions
python thumbnail_bot.py remix URL --niche "credit repair" --title "How I fixed a 520 score" --face me.jpg -n 3

# Find viral outliers (views far above channel size) — needs YOUTUBE_API_KEY
python thumbnail_bot.py find "side hustle" --top 10

# Find outliers in one niche and remix them all into yours
python thumbnail_bot.py find "side hustle" --top 5 --remix-niche "options trading"

# Telegram bot
python telegram_bot.py
#   send a photo with your title as the caption → similar mode
#   /similar <title>  → similar mode with no photo
#   send:  https://youtu.be/xxx | real estate | My first rental at 21
#   photo captioned /face  → puts you in every thumbnail
```

## `similar` output (`out/similar_<title>_<time>/`)
- `references.jpg`: contact sheet of the viral thumbnails it studied, plus `refs/` and `references.json` (views, outlier ratio)
- `plan.json`: shared viral formula, why each reference works, how to use your image, title options, concepts
- `thumb_N_<concept>.jpg`: 1280x720 thumbnails built around your image, each using one reference's layout

`similar` needs `YOUTUBE_API_KEY`. Videos are ranked by views combined with how far they beat their channel's subscriber count.

## `remix` output (`out/<id>_<niche>_<time>/`)
- `original.jpg`: source thumbnail
- `analysis.json`: hook, layout, palette, text, emotion, and why it works
- `plan.json`: new title, 3 alt titles, overlay text, image prompts
- `thumb_1.jpg …`: 1280x720 thumbnails, ready to upload

## How it works
1. Pulls the thumbnail and title from YouTube (no key needed).
2. Claude vision breaks down the hook mechanism, composition, subject scale, text, palette and graphic elements.
3. Claude rewrites the concept for your niche, keeping the structure and swapping the content.
4. gpt-image-1 generates it, using the original as a layout reference only (and your face if you gave one).

Flags: `--no-ref` for text-only generation (looser match). `--quality medium` for cheaper and faster.
Cost is about $0.17–0.25 per high-quality thumbnail plus a few cents of Claude.
