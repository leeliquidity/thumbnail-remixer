#!/usr/bin/env python3
"""
Higgsfield example: Seedance 2.5 text-to-video.

Credentials: put HF_KEY=<key-id>:<key-secret> in .env.local (git-ignored).
This script never prints or logs the key.

Run:  python main.py
Note: every run is a billable generation.
"""
import sys

from dotenv import load_dotenv

load_dotenv(".env.local")

import higgsfield_client  # noqa: E402  (import after env is loaded)
from higgsfield_client import NSFW, Cancelled, Completed, Failed, InProgress, Queued  # noqa: E402
from higgsfield_client.exceptions import CredentialsMissedError, HiggsfieldClientError  # noqa: E402

MODEL = "bytedance/seedance-2.5/text-to-video"
ARGUMENTS = {
    "prompt": "A cinematic scene at sunset",
    "duration": 5,
    "resolution": "720p",
    "aspect_ratio": "16:9",
}

STATUS_LABEL = {Queued: "queued", InProgress: "in progress", Completed: "completed",
                Failed: "failed", NSFW: "moderated (nsfw)", Cancelled: "canceled"}


def video_url(result: dict) -> str | None:
    video = result.get("video")
    if isinstance(video, dict):
        return video.get("url")
    if isinstance(video, str):
        return video
    if isinstance(video, list) and video:
        first = video[0]
        return first.get("url") if isinstance(first, dict) else first
    return None


def main() -> int:
    last = {"s": None}

    def on_update(status):
        label = STATUS_LABEL.get(type(status), type(status).__name__)
        if label != last["s"]:
            print(f"status: {label}", flush=True)
            last["s"] = label

    try:
        result = higgsfield_client.subscribe(
            MODEL,
            arguments=ARGUMENTS,
            on_enqueue=lambda rid: print(f"request id: {rid}", flush=True),
            on_queue_update=on_update,
        )
    except CredentialsMissedError:
        print("ERROR: HF_KEY is not set. Add HF_KEY=<key-id>:<key-secret> to .env.local.", file=sys.stderr)
        return 2
    except HiggsfieldClientError as e:
        print(f"ERROR: Higgsfield API error: {e}", file=sys.stderr)
        return 1

    status = result.get("status")
    if status == "completed":
        url = video_url(result)
        if url:
            print(f"video url: {url}")
            return 0
        print("ERROR: request completed but no video URL was returned.", file=sys.stderr)
        return 1
    if status == "nsfw":
        print("FAILED: the request was moderated (flagged NSFW). No video was produced.", file=sys.stderr)
    elif status == "canceled":
        print("FAILED: the request was canceled. No video was produced.", file=sys.stderr)
    elif status == "failed":
        print("FAILED: generation failed on Higgsfield's side. No video was produced.", file=sys.stderr)
    else:
        print(f"FAILED: unexpected final status {status!r}. No video was produced.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
