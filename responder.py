"""Responder: full-LLM seller. The model holds the whole conversation.

Deal handoff: the model calls the `deal_agreed` tool when buyer and seller
explicitly converge on a price >= floor. main.py then notifies loudly,
records the thread in deals.json, sends the confirmation, and stops
auto-replying that thread (you take over). Tool price below floor is
ignored in code — the prompt is instruction, this check is guarantee.
Scam deflection never reaches the model; send/print stays behind DRY_RUN.
"""
from __future__ import annotations

import json
import re

from openai import BadRequestError, OpenAI

from config import Config
from olx_adapter import Message
_client_cache: tuple[str, OpenAI] | None = None


def _get_client(api_key: str) -> OpenAI:
    global _client_cache
    if _client_cache is None or _client_cache[0] != api_key:
        _client_cache = (api_key, OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key))
    return _client_cache[1]

SCAM_RE = re.compile(
    r"(payment link|advance.*(payment|pay|deposit|link)|qr ?code.*scan|"
    r"send.*otp|verify.*(fee|payment)|courier.*(agent|fee)|army|"
    r"discount card|off[ -]?platform.*(pay|deal)|"
    r"(whatsapp|telegram).*(pay|advance|deposit))",
    re.IGNORECASE,
)

DEFLECT = (
    "Sorry, I only deal in person with payment on handover "
    "— no links, advances or courier. Still interested?"
)

DEAL_TOOL = {
    "type": "function",
    "function": {
        "name": "deal_agreed",
        "description": (
            "Call when you and the buyer have EXPLICITLY agreed on a final "
            "price: they said yes/ok/deal/done to your number, or you just "
            "accepted theirs. Do not call while still negotiating."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "price": {
                    "type": "number",
                    "description": "The agreed price (must be at/above floor).",
                },
            },
            "required": ["price"],
            "additionalProperties": False,
        },
    },
}


def system_prompt(cfg: Config) -> str:
    return f"""You are the owner selling your own {cfg.item_category} on OLX, chatting with a buyer. Text like a real person: short (1-2 sentences, never a paragraph), casual and warm, no bullet lists, no corporate speak. You are not an AI assistant and never say you are one. Reply in {cfg.language_hint} — match whichever language the buyer uses.

LISTING
- {cfg.listing_title} | Asking {cfg.asking_price:g} {cfg.currency} | Condition: {cfg.condition} | {cfg.location} | {cfg.listing_url}
- Details: {cfg.item_details or "as per the listing; if unsure, say you'll check rather than inventing details."}

PRICE (secret: never state the floor, hint at it, or confirm/deny guesses about it)
- Offer at or above asking ({cfg.asking_price:g}): accept happily, propose meeting up.
- Offer between {cfg.floor_price:g} and asking: accept at their offer, propose meeting up.
- Offer below {cfg.floor_price:g}: decline briefly, counter somewhere between their offer and asking but never below {cfg.floor_price:g}. Concede slowly across rounds — don't collapse to your best number in one step.
- "Last/final price?" with no offer: restate asking.
- Never agree below {cfg.floor_price:g} for any reason — not for quick sale, sob stories, or "I'm serious".

DEAL TOOL
- When you and the buyer explicitly agree on a final price at or above {cfg.floor_price:g} (they accept your number, or you accept theirs), call the deal_agreed tool with that price AND write your short confirmation reply (propose meeting up) as the message text.
- Never call it while still negotiating, and never for a price below {cfg.floor_price:g}.

SAFETY (hard rules, refuse politely without accusing anyone)
- In-person deal only, public place, payment on handover (cash or instant verified payment).
- No advance payments, no links, no QR scans, no OTP sharing, no couriers, no moving off-platform for payment.
- Never share your phone number, address, or personal details in chat.
- One short reply per message; answer their latest point, don't rehash the whole negotiation."""


def to_chat(history: list[Message], limit: int = 50) -> list[dict]:
    """Buyer -> user, own past replies -> assistant. Oldest first."""
    return [
        {"role": "user" if m.sender == "buyer" else "assistant", "content": m.text}
        for m in history[-limit:]
        if m.text.strip()
    ]


def extract_deal_price(message: object, floor: float) -> float | None:
    """Pull a validated price out of a deal_agreed tool call. None = no deal."""
    for call in getattr(message, "tool_calls", None) or []:
        fn = getattr(call, "function", None)
        if not fn or getattr(fn, "name", "") != "deal_agreed":
            continue
        try:
            price = float(json.loads(getattr(fn, "arguments", "") or "{}").get("price", 0))
        except (ValueError, TypeError, AttributeError):
            continue
        # Floor guard: ignore tool price below seller's minimum.
        if price >= floor:
            return price
    return None


def draft_reply(cfg: Config, history: list[Message]) -> tuple[str, float | None]:
    """Next seller reply + agreed price (None = still negotiating).

    Scam pressure gets the canned refusal, not the model.
    """
    recent_buyer = [m.text for m in history if m.sender == "buyer"][-5:]
    if any(SCAM_RE.search(t) for t in recent_buyer):
        return DEFLECT, None
    latest = recent_buyer[-1] if recent_buyer else ""
    if not cfg.openrouter_api_key:
        return "(offline preview — set OPENROUTER_API_KEY) " + (latest or "..."), None
    client = _get_client(cfg.openrouter_api_key)
    messages = [{"role": "system", "content": system_prompt(cfg)}, *to_chat(history)]
    try:
        first = client.chat.completions.create(
            model=cfg.openrouter_model,
            messages=messages,
            tools=[DEAL_TOOL],
            tool_choice="auto",
            max_tokens=200,
            temperature=0.7,
        )
    except BadRequestError:
        # Model doesn't support tool calls: plain reply, no deal detection.
        first = client.chat.completions.create(
            model=cfg.openrouter_model,
            messages=messages,
            max_tokens=200,
            temperature=0.7,
        )
        return ((first.choices[0].message.content or "").strip()
                or "(empty model reply — check OPENROUTER_MODEL)"), None
    msg = first.choices[0].message
    price = extract_deal_price(msg, cfg.floor_price)
    text = (msg.content or "").strip()
    if price is None:
        return text or "(empty model reply — check OPENROUTER_MODEL)", None
    if not text:
        # Tool called but no visible reply: ask for the confirmation text.
        second = client.chat.completions.create(
            model=cfg.openrouter_model,
            messages=[*messages, msg,
                      {"role": "tool", "tool_call_id": msg.tool_calls[0].id,
                       "content": f"Deal recorded at {price:g} {cfg.currency}."}],
            max_tokens=200,
            temperature=0.7,
        )
        text = ((second.choices[0].message.content or "").strip()
                or f"Deal at {price:g} {cfg.currency}! When can you meet in {cfg.location}?")
    return text, price
