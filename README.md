# OLX seller bot — LLM auto-replies to your classifieds chats

Polls your OLX inbox over plain HTTPS, drafts replies with any
[OpenRouter](https://openrouter.ai) chat model from the full thread history,
and sends them through the real chat page. You set asking + secret floor
prices; the model haggles within them and calls a `deal_agreed` tool when a
price locks in — then you take over.

Built and run live against `www.olx.in` (Sep 2026, ~17 threads answered);
other OLX country domains share the same chat stack.

## How it works

- **Reads** (`olx_adapter.py`): `GET xchat.olx.in/user/threads` (v3) +
  `POST xchat.olx.in/user/messages` (v8, `[{peer_id, ad_id, time: 0}]`,
  batches of ≤4). Auth: `x-access-token` == `_bifro_jwt` cookie.
  Unread = buyer messages with status `received`.
- **Replies** (`responder.py`): full thread history (buyer → user, your past
  replies → assistant) + listing facts, secret price ladder, and item details
  in one system prompt. Scam pressure gets a canned refusal without touching
  the model; `deal_agreed` prices below floor are rejected in code.
- **Sends**: XMPP first, stealth headless Chrome fallback
  (`nf/chat/user/{peer}/{ad}`, composer fill + `btnSend` DOM click).
- **Loop** (`main.py`): poll → one reply per thread (latest only) →
  send/print. `state.json` remembers answered threads across restarts;
  deals go to `deals.json`, stop auto-reply, print 🤝 + bell + chat URL.
- **Tokens**: `x-access-token` ~hours, `ct` ~24h. On 401/403 re-export both
  from devtools and restart — the bot cannot self-refresh.

## Setup

```powershell
uv venv
uv sync   # uses installed Chrome via channel="chrome"; no browser download
Copy-Item .env.example .env   # then fill it in
```

Tokens: while logged in at `<your-domain>/nf/chat`, devtools on any
`xchat.olx.in` request → `x-access-token` header = `XCHAT_TOKEN`;
`ct` cookie = `CHAT_TOKEN`. Get `AD_ID` from your listing URL digits and an
[OpenRouter API key](https://openrouter.ai/keys).

## Run

```powershell
uv run python test_responder.py   # prompt/role/tool checks, no network
uv run python test_live.py        # read-only live xchat check (needs tokens)
uv run python main.py             # DRY_RUN=1 prints; DRY_RUN=0 sends for real
```

## `.env`

`XCHAT_TOKEN, CHAT_TOKEN, AD_ID, OLX_BASE_URL, OPENROUTER_MODEL (any
OpenRouter chat id), OPENROUTER_API_KEY, ITEM_CATEGORY, LISTING_TITLE,
LISTING_URL, ASKING_PRICE, FLOOR_PRICE, CURRENCY, CONDITION, LOCATION,
ITEM_DETAILS, LANGUAGE_HINT, DRY_RUN, POLL_SECONDS`.

## Risks & honesty

- Unofficial HTTP reads + browser-driven sends: against OLX ToS in spirit.
  Single ad, slow polling (60s+), `DRY_RUN=1` first.
- The floor lives in the prompt: the model is ordered never to state, hint,
  or confirm/deny it, but that's instruction, not guarantee.
- Token expiry / CAPTCHA / ban possible. Never committed (git-ignored):
  `.env`, `.session.json`, `state.json`, `deals.json`, `.olx-profile/`.
