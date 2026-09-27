"""Live xchat checks (read-only). Needs XCHAT_TOKEN + AD_ID in env.

Usage: uv run python test_live.py
Skips (exit 0) when XCHAT_TOKEN is missing so CI stays green.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")

import config
from olx_adapter import OlxAdapter

os.environ.setdefault("ASKING_PRICE", "1")
os.environ.setdefault("FLOOR_PRICE", "1")
os.environ.setdefault("OPENROUTER_MODEL", "test-model")

token = os.environ.get("XCHAT_TOKEN", "")
if not token:
    print("SKIP: XCHAT_TOKEN not set (read-only live checks need a fresh token).")
    sys.exit(0)

cfg = config.load()
a = OlxAdapter(cfg.xchat_token, cfg.olx_base_url)
print("user_id:", a.user_id)
threads = a.threads()
print("threads:", len(threads))
mine = [t for t in threads if str(t["ad_id"]) == str(cfg.ad_id)]
print(f"threads on ad {cfg.ad_id}:", len(mine))
unread = a.poll_unread(ad_id=cfg.ad_id)
print("unread buyer msgs:", len(unread))
for m in unread[:10]:
    print(f"  [{m.thread_id} {m.timestamp}] {m.text!r}")
print("LIVE GREEN")
