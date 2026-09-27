"""Main loop: poll xchat HTTP, LLM drafts from full thread history, send (or print).

Deal handoff: when the model calls deal_agreed with a price >= floor, the
thread is recorded in deals.json, the confirmation goes out, auto-reply
stops for that thread (you take over), and the terminal rings the bell.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import config
import responder
from olx_adapter import OlxAdapter, TokenExpired

STATE_FILE = "state.json"
DEALS_FILE = "deals.json"


@dataclass
class State:
    seen_ids: set[str] = field(default_factory=set)  # msg_ids already answered (memory only)
    handed_off: set[str] = field(default_factory=set)  # deal threads, reloaded from deals.json
    answered_threads: set[str] = field(default_factory=set)  # threads answered this run AND past runs


def load_state(state: State) -> None:
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    state.handed_off.update(data.get("handed_off", []))
    state.answered_threads.update(data.get("answered_threads", []))
    for row in data.get("deals", []):
        try:
            state.handed_off.add(row["thread_id"])
        except KeyError:
            pass


def save_state(state: State) -> None:
    # deals.json stays the readable deal ledger; state.json is the resume file.
    try:
        with open(DEALS_FILE) as f:
            deals = json.load(f)
    except (OSError, ValueError):
        deals = []
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"handed_off": sorted(state.handed_off),
                   "answered_threads": sorted(state.answered_threads),
                   "deals": deals}, f, indent=1)
    os.replace(tmp, STATE_FILE)


def load_handed_off(state: State) -> None:
    # Back-compat: deals.json written before state.json existed.
    try:
        with open(DEALS_FILE) as f:
            rows = json.load(f)
        for row in rows:
            state.handed_off.add(row["thread_id"])
    except (OSError, ValueError, KeyError):
        pass


def record_deal(state: State, thread_id: str, price: float, currency: str) -> None:
    rows = []
    try:
        with open(DEALS_FILE) as f:
            rows = json.load(f)
    except (OSError, ValueError):
        pass
    rows.append({"thread_id": thread_id, "price": price, "currency": currency,
                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    tmp = DEALS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f, indent=1)
    os.replace(tmp, DEALS_FILE)


def main() -> None:
    cfg = config.load()
    poll_seconds = max(cfg.poll_seconds, 10)
    state = State()
    load_state(state)
    load_handed_off(state)
    adapter = OlxAdapter(cfg.xchat_token, cfg.olx_base_url, chat_token=cfg.chat_token)
    print(f"Logged in as user {adapter.user_id}; model {cfg.openrouter_model}; "
          f"polling ad {cfg.ad_id} every {poll_seconds}s (dry_run={cfg.dry_run}). Ctrl+C to stop.")
    while True:
        try:
            unread = adapter.poll_unread(ad_id=cfg.ad_id)
            # One reply per thread: group by thread, answer only the latest
            # buyer message in each. Threads answered on a previous run are
            # skipped — backlog is NOT re-answered on restart.
            latest: dict[str, object] = {}
            for m in unread:
                if m.msg_id and m.msg_id in state.seen_ids:
                    continue
                prev = latest.get(m.thread_id)
                if prev is None or m.timestamp > prev.timestamp:
                    latest[m.thread_id] = m
            for m in sorted(latest.values(), key=lambda x: x.timestamp):
                if m.msg_id:
                    state.seen_ids.add(m.msg_id)
                if m.thread_id in state.handed_off:
                    print(f"[{m.thread_id}] handed off — you take it from here: {m.text!r}")
                    continue
                if m.thread_id in state.answered_threads:
                    print(f"[{m.thread_id}] already answered earlier — skipped: {m.text!r}")
                    continue
                history = adapter.thread_history(m.thread_id)
                reply, deal_price = responder.draft_reply(cfg, history)
                if deal_price is not None:
                    adapter.send(m.thread_id, reply, dry_run=cfg.dry_run)
                    state.handed_off.add(m.thread_id)
                    state.answered_threads.add(m.thread_id)
                    record_deal(state, m.thread_id, deal_price, cfg.currency)
                    save_state(state)
                    print(f"\a🤝 DEAL {deal_price:g} {cfg.currency} [{m.thread_id}] — "
                          f"confirmation: {reply!r}\n   TAKE OVER: open "
                          f"{cfg.olx_base_url}/nf/chat/user/{m.thread_id.split(':')[1]}/{m.thread_id.split(':')[0]}")
                    continue
                adapter.send(m.thread_id, reply, dry_run=cfg.dry_run)
                state.answered_threads.add(m.thread_id)
                save_state(state)
                print(f"[{m.thread_id}] buyer: {m.text!r} -> {reply!r}")
        except TokenExpired as e:
            print(f"FATAL: {e}")
            break
        except Exception as e:  # keep the loop alive; transient network hiccups
            print(f"poll error: {e}")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
