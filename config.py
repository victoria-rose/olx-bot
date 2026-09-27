"""Config: all seller authority lives here. Floor price never leaves this machine."""
from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    openrouter_api_key: str
    openrouter_model: str
    xchat_token: str
    chat_token: str
    ad_id: str
    olx_base_url: str
    # The listing being sold — any category, any OLX country domain.
    item_category: str  # e.g. "used phone", "car", "sofa", "flat for rent"
    listing_title: str
    asking_price: float
    floor_price: float
    currency: str
    condition: str
    location: str
    listing_url: str
    item_details: str  # free-form specs the LLM answers questions from
    language_hint: str  # buyer languages to match, e.g. "English, Hindi"
    dry_run: bool
    poll_seconds: int


def load() -> Config:
    load_dotenv()
    asking = float(os.environ.get("ASKING_PRICE", "0") or 0)
    floor = float(os.environ.get("FLOOR_PRICE", "0") or 0)
    if asking <= 0 or floor <= 0:
        raise SystemExit("Set ASKING_PRICE and FLOOR_PRICE in .env (copy .env.example).")
    if floor > asking:
        raise SystemExit("FLOOR_PRICE must be <= ASKING_PRICE.")
    model = os.environ.get("OPENROUTER_MODEL", "")
    if not model:
        raise SystemExit("Set OPENROUTER_MODEL in .env (any OpenRouter model id).")
    ad_id = os.environ.get("AD_ID", "")
    if not ad_id:
        raise SystemExit("Set AD_ID in .env (digits in your listing URL).")
    return Config(
        openrouter_model=model,
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        xchat_token=os.environ.get("XCHAT_TOKEN", ""),
        chat_token=os.environ.get("CHAT_TOKEN", ""),
        ad_id=ad_id,
        olx_base_url=os.environ.get("OLX_BASE_URL", "https://www.olx.in").rstrip("/"),
        item_category=os.environ.get("ITEM_CATEGORY", "used item"),
        listing_title=os.environ.get("LISTING_TITLE", ""),
        asking_price=asking,
        floor_price=floor,
        currency=os.environ.get("CURRENCY", "INR"),
        condition=os.environ.get("CONDITION", ""),
        location=os.environ.get("LOCATION", ""),
        listing_url=os.environ.get("LISTING_URL", ""),
        item_details=os.environ.get("ITEM_DETAILS",
                                    os.environ.get("PRODUCT_SPECS", "")),
        language_hint=os.environ.get("LANGUAGE_HINT", "the buyer's language"),
        dry_run=os.environ.get("DRY_RUN", "1") != "0",
        poll_seconds=int(os.environ.get("POLL_SECONDS", "60") or 60),
    )
