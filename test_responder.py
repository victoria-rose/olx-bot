"""Offline checks for the full-LLM responder. Run: uv run python test_responder.py"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from config import Config
from olx_adapter import Message
import responder

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name, extra)
    if not cond:
        fails.append(name)


def make_cfg(**over):
    base = dict(openrouter_api_key="", openrouter_model="test-model",
                xchat_token="tok", chat_token="ctok", ad_id="123",
                olx_base_url="https://www.olx.in", item_category="used phone",
                listing_title="Phone 4/64", asking_price=25000, floor_price=22000,
                currency="INR", condition="good", location="City",
                listing_url="http://x",
                item_details="AMOLED 90Hz, box & charger, battery 5000mAh",
                language_hint="English", dry_run=True, poll_seconds=60)
    base.update(over)
    return Config(**base)


cfg = make_cfg()

hist = [
    Message("t", "buyer", "Hi, is this available?", "m1", 1),
    Message("t", "me", "Yes, available!", "m2", 2),
    Message("t", "buyer", "20000 cash today", "m3", 3),
]

sys_p = responder.system_prompt(cfg)
check("floor in prompt (LLM needs authority)", "22000" in sys_p)
check("never-reveal order present", "never state the floor" in sys_p.lower())
check("details in prompt", "AMOLED" in sys_p)
check("category in prompt", "used phone" in sys_p)
check("language hint in prompt", "English" in sys_p)
check("no phone-specific defaults", "Telugu" not in sys_p and "Hinglish" not in sys_p)

sofa = make_cfg(item_category="sofa", listing_title="3-seater fabric sofa",
                item_details="Grey fabric, 2 years old, pickup only")
check("prompt generalizes to furniture",
      "sofa" in responder.system_prompt(sofa).lower())

chat = responder.to_chat(hist)
check("role mapping buyer->user me->assistant",
      [m["role"] for m in chat] == ["user", "assistant", "user"], str(chat))
check("oldest-first order", chat[0]["content"].startswith("Hi"))

r, deal = responder.draft_reply(cfg, hist)
check("offline preview, no deal", r.startswith("(offline preview") and deal is None, repr(r))

scam = hist + [Message("t", "buyer", "pay advance via link, send otp", "m4", 4)]
r, deal = responder.draft_reply(cfg, scam)
check("scam bypasses LLM", r == responder.DEFLECT and deal is None)

empty = make_cfg(item_details="")
check("missing details fall back to honesty line",
      "inventing" in responder.system_prompt(empty))

check("deal tool order in prompt", "deal_agreed" in responder.system_prompt(cfg))
check("tool schema named deal_agreed",
      responder.DEAL_TOOL["function"]["name"] == "deal_agreed")


class _Fn:
    def __init__(self, name, args):
        self.name = name
        self.arguments = args


class _Call:
    def __init__(self, fn):
        self.function = fn


class _Msg:
    def __init__(self, calls):
        self.tool_calls = calls


ok = _Msg([_Call(_Fn("deal_agreed", '{"price": 23000}'))])
check("valid tool price accepted", responder.extract_deal_price(ok, 22000) == 23000)
low = _Msg([_Call(_Fn("deal_agreed", '{"price": 15000}'))])
check("below-floor tool price rejected", responder.extract_deal_price(low, 22000) is None)
junk = _Msg([_Call(_Fn("deal_agreed", 'not json'))])
check("malformed tool args rejected", responder.extract_deal_price(junk, 22000) is None)
other = _Msg([_Call(_Fn("something_else", '{"price": 23000}'))])
check("wrong tool name ignored", responder.extract_deal_price(other, 22000) is None)
check("no tool calls, no deal", responder.extract_deal_price(_Msg([]), 22000) is None)

print(f"\n{len(fails)} failures" if fails else "\nALL GREEN")
sys.exit(1 if fails else 0)
