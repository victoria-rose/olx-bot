"""OLX xchat adapter — reverse-engineered 2026-09-27 against www.olx.in.
Other OLX country domains share the same stack; pass yours as base_url and
the chat host constants below follow the same pattern.

READ path (verified live, pure HTTPS, no browser needed):
  GET  https://xchat.olx.in/user/threads?limit=50&minLimit=30   (x-accept-version: 3)
  POST https://xchat.olx.in/user/messages                      (x-accept-version: 8)
       body: [{"peer_id": <int>, "ad_id": <int>, "time": 0}], chunks of <=4
       (a full 12-thread batch 500s). Auth: x-access-token == _bifro_jwt.
  Unread heuristic: buyer messages with status "received" ([INFERENCE]:
  browser-open thread reads "displayed", inbox-unread reads "received").

SEND path: XMPP attempted first, stealth-headless-Chrome fallback delivers.
  XMPP: wss://msg.olx.in/ws/chat (RFC 7395, subprotocol xmpp), SASL PLAIN
  authcid "<uid>_olxin" / `ct`-cookie password, reopen+bind pipelined, then a
  <message> stanza with extras itemId/countryId/senderType. As of 2026-09-27
  the bind IQ gets connection-timeout from plain-Python stacks (auth itself
  succeeds), so _xmpp_send is aspirational and _browser_send does the work:
  real thread page nf/chat/user/{peer}/{ad}, textarea[data-aut-id=inputBox]
  fill + [data-aut-id=btnSend] DOM click (overlay eats Playwright clicks).
  Headless Chrome needs headed UA + hidden webdriver or Akamai kills TLS
  (ERR_HTTP2_PROTOCOL_ERROR); the on-disk profile must hold fresh essential
  cookies (ct/rt/user/bifro/lf) or the page bounces to /account — _browser()
  reseeds them from the live session on launch.

Session notes: x-access-token (~hours) and `ct` (24h) both expire; on 401/403
re-export from devtools into XCHAT_TOKEN / CHAT_TOKEN. Own user id resolves
via GET /user/preferences (verified 200).
"""
from __future__ import annotations

import base64
import json
import time
import urllib.request
import uuid
from dataclasses import dataclass
XCHAT = "https://xchat.olx.in"
XMPP_WS = "wss://msg.olx.in/ws/chat"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"

# Verified in live DOM: composer on /nf/chat/user/{peer}/{ad}.
COMPOSER = 'textarea[data-aut-id="inputBox"]'
SEND_BTN = '[data-aut-id="btnSend"]'
# Akamai kills headless-Chrome TLS (ERR_HTTP2_PROTOCOL_ERROR); headed UA +
# hidden webdriver passes (verified 2026-09-27).
STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


@dataclass
class Message:
    thread_id: str  # "ad_id:peer_id"
    sender: str  # "buyer" | "me"
    text: str
    msg_id: str = ""
    timestamp: int = 0


class TokenExpired(RuntimeError):
    pass


class OlxAdapter:
    def __init__(self, xchat_token: str, base_url: str = "https://www.olx.in",
                 profile_dir: str = ".olx-profile", user_id: str | None = None,
                 chat_token: str | None = None):
        if not xchat_token:
            raise SystemExit("Set XCHAT_TOKEN in .env (x-access-token == _bifro_jwt cookie).")
        self.token = xchat_token.strip()
        self.base_url = base_url.rstrip("/")
        self.profile_dir = profile_dir
        self._user_id = user_id
        self.chat_token = (chat_token or "").strip()  # `ct` cookie: XMPP password
        self._pw = None  # one Playwright browser per process (profile lock)

    # ---- HTTP layer ----
    def _call(self, path: str, data: object = None, version: str | None = None) -> object:
        body = json.dumps(data).encode() if data is not None else None
        headers = {"accept": "application/json", "content-type": "application/json",
                   "x-access-token": self.token, "User-Agent": UA}
        if version:
            headers["x-accept-version"] = version
        req = urllib.request.Request(XCHAT + path, data=body,
                                     method="POST" if body else "GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise TokenExpired(
                    "xchat rejected the token (403). Re-export _bifro_jwt "
                    "(devtools on any xchat request -> x-access-token) into XCHAT_TOKEN.") from e
            raise

    @property
    def user_id(self) -> str:
        if not self._user_id:
            prefs = self._call("/user/preferences")
            self._user_id = str(prefs["user_id"])
        return self._user_id

    # ---- reads ----
    def threads(self, limit: int = 50) -> list[dict]:
        payload = self._call(f"/user/threads?limit={limit}&minLimit=30", version="3")
        return payload["data"]

    def poll_unread(self, ad_id: str | int | None = None) -> list[Message]:
        """Buyer messages with status 'received' (vs 'displayed' = seen)."""
        # [INFERENCE] on status semantics: browser-open thread read 'displayed';
        # inbox-unread threads read 'received'. main.py also dedupes by msg_id,
        # so a repeat is printed, never double-sent.
        me = self.user_id
        threads = self.threads()
        if ad_id is not None:
            threads = [t for t in threads if str(t["ad_id"]) == str(ad_id)]
        if not threads:
            return []
        out: list[Message] = []
        # /user/messages 500s on a full 12-thread batch (verified); chunk it.
        for i in range(0, len(threads), 4):
            batch = [{"peer_id": int(t["peer_id"]), "ad_id": int(t["ad_id"]), "time": 0}
                     for t in threads[i:i + 4]]
            payload = self._call("/user/messages", batch, version="8")
            for conv in payload["data"]:
                if not conv.get("success"):
                    continue
                tid = f"{conv['ad_id']}:{conv['peer_id']}"
                for m in conv.get("messages", []) or []:
                    if str(m["sender_id"]) == me:
                        continue
                    if m.get("status") != "received":
                        continue
                    out.append(Message(tid, "buyer", m.get("text") or "",
                                       m.get("msg_id", ""), int(m.get("timestamp", 0))))
        out.sort(key=lambda m: m.timestamp)
        return out

    def thread_history(self, thread_id: str, limit: int = 50) -> list[Message]:
        """Full conversation for one thread, oldest first. LLM context."""
        me = self.user_id
        ad_id, _, peer_id = thread_id.partition(":")
        payload = self._call("/user/messages",
                             [{"peer_id": int(peer_id), "ad_id": int(ad_id), "time": 0}],
                             version="8")
        out: list[Message] = []
        for conv in payload["data"]:
            for m in conv.get("messages", []) or []:
                text = (m.get("text") or "").strip()
                if not text:
                    continue  # images/voice/location have no text
                sender = "me" if str(m["sender_id"]) == me else "buyer"
                out.append(Message(thread_id, sender, text,
                                   m.get("msg_id", ""), int(m.get("timestamp", 0))))
        out.sort(key=lambda m: m.timestamp)
        return out[-limit:]

    # ---- session bootstrap (manual OTP, once) ----
    def login_once(self) -> None:
        """Headed browser; you log in yourself. Session persists in profile_dir."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                self.profile_dir, headless=False, channel="chrome",
                user_agent=UA)
            ctx.add_init_script(STEALTH_JS)
            page = ctx.new_page()
            page.goto(f"{self.base_url}/nf/chat", wait_until="domcontentloaded")
            print("Log in manually in the opened browser. Press Enter here when chat loads.")
            input()
            ctx.close()

    # ---- send: raw XMPP (primary) + browser fallback ----
    def _xmpp_send(self, peer_id: str, ad_id: str, text: str) -> None:
        """Deliver one chat message over wss://msg.olx.in/ws/chat.

        Fully synchronous (websocket-client): open -> SASL PLAIN
        (authcid "<uid>_olxin", password = `ct` cookie) -> reopen + bind IQ
        pipelined -> message stanza. No asyncio, so Playwright sync keeps
        working in the same process.
        """
        import websocket  # websocket-client, sync

        me = self.user_id
        authcid = f"{me}_olxin"
        password = self.chat_token
        if not password:
            raise RuntimeError("XMPP send needs the `ct`-cookie chatToken: set CHAT_TOKEN in .env.")
        msg_id = str(uuid.uuid4())
        stamp = int(time.time() * 1000)

        ws = websocket.create_connection(
            XMPP_WS, subprotocols=["xmpp"], origin=self.base_url,
            header={"User-Agent": UA}, timeout=60)
        try:
            ws.send('<open xmlns="urn:ietf:params:xml:ns:xmpp-framing" '
                    'version="1.0" xml:lang="en" to="olxin"/>')
            phase = "open"
            b64 = base64.b64encode(f"\x00{authcid}\x00{password}".encode()).decode()
            deadline = time.time() + 60
            while time.time() < deadline:
                msg = ws.recv()
                if "<mechanisms" in msg and phase == "open":
                    ws.send(f'<auth xmlns="urn:ietf:params:xml:ns:xmpp-sasl" '
                            f'mechanism="PLAIN">{b64}</auth>')
                    phase = "auth"
                elif "<success" in msg and phase == "auth":
                    rid = str(uuid.uuid4())
                    ws.send('<open xmlns="urn:ietf:params:xml:ns:xmpp-framing" '
                            'version="1.0" xml:lang="en" to="olxin"/>')
                    ws.send(f'<iq xmlns="jabber:client" type="set" id="{rid}">'
                            f'<bind xmlns="urn:ietf:params:xml:ns:bind">'
                            f'<resource>pwaDesktop-{stamp}</resource>'
                            f'</bind></iq>')
                    phase = "bind"
                elif phase == "bind":
                    if "<jid>" in msg:
                        jid = msg.split("<jid>")[1].split("</jid>")[0]
                        body = (text.replace("&", "&amp;").replace("<", "&lt;")
                                .replace(">", "&gt;"))
                        ws.send(
                            f'<message xmlns="jabber:client" '
                            f'to="{peer_id}_olxin@olxin" from="{jid}" '
                            f'type="chat" id="{msg_id}">'
                            f'<body>{body}</body>'
                            f'<extras xmlns="urn:xmpp:extras" countryId="olxin" '
                            f'itemId="{ad_id}" senderType="seller" '
                            f'creation_source="panamera" app_key="panamera"/>'
                            f'<markable xmlns="urn:xmpp:chat-markers:0"/>'
                            f'</message>')
                        phase = "sent"
                    elif "<failure" in msg or "<stream:error" in msg:
                        raise RuntimeError(f"XMPP bind failed: {msg[:200]}")
                elif phase == "sent":
                    ws.close()
                    return  # server acked; delivery done
                elif "<failure" in msg or "<stream:error" in msg:
                    raise RuntimeError(f"XMPP setup failed: {msg[:200]}")
            raise RuntimeError("XMPP send got no bind result")
        finally:
            try:
                ws.close()
            except Exception:
                pass
    def _browser(self):
        """Lazily launched stealth headless Chrome; one per process."""
        if self._pw is None:
            import atexit

            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._ctx = self._pw.chromium.launch_persistent_context(
                self.profile_dir, headless=True, channel="chrome",
                user_agent=UA)
            self._ctx.add_init_script(STEALTH_JS)
            atexit.register(self._teardown_browser)
            # Seed login cookies from the live session: the on-disk profile
            # goes stale whenever tokens refresh, which bounces to /account.
            try:
                import config as _cfg
                import json as _json

                env = _cfg.load()
                try:
                    saved = _json.load(open(".session.json"))["cookies"]
                except Exception:
                    saved = []
                jar = [
                    {"name": c["name"], "value": c["value"], "domain": c["domain"],
                     "path": c.get("path") or "/",
                     "secure": bool(c.get("secure")),
                     "httpOnly": bool(c.get("httpOnly", False)),
                     "sameSite": c.get("sameSite") or "Lax"}
                    for c in saved
                    if c["name"] in {"ct", "rt", "user", "_bifro_jwt", "lf"}
                ]
                if env.chat_token and not any(c["name"] == "ct" for c in jar):
                    jar.append({"name": "ct", "value": env.chat_token,
                                "domain": ".olx.in", "path": "/",
                                "secure": True, "httpOnly": False, "sameSite": "Lax"})
                if jar:
                    self._ctx.add_cookies(jar)
            except Exception:
                pass
        return self._ctx

    def _browser_send(self, thread_id: str, text: str) -> None:
        """Fallback: drive the real thread page in stealth headless Chrome."""
        ad_id, _, peer_id = thread_id.partition(":")
        url = f"{self.base_url}/nf/chat/user/{peer_id}/{ad_id}"
        last: Exception | None = None
        for attempt in range(2):
            try:
                page = self._browser().new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector(COMPOSER, timeout=20000)
                    try:
                        page.get_by_text("Continue to chat", exact=True).click(timeout=3000)
                    except Exception:
                        pass
                    page.fill(COMPOSER, text)
                    # btnSend only swaps in after typing (else btnStartRecording);
                    # and an overlay intercepts pointer events, so Playwright's
                    # actionability click times out — DOM .click() works (verified).
                    page.wait_for_selector(SEND_BTN, timeout=10000)
                    page.evaluate("() => document.querySelector('[data-aut-id=\"btnSend\"]').click()")
                    page.wait_for_timeout(4000)
                finally:
                    page.close()
                return
            except Exception as e:
                last = e
                print(f"browser send retry {attempt}: {str(e)[:150]}")
                self._teardown_browser()
        raise RuntimeError(f"browser send failed: {last}")

    def _teardown_browser(self) -> None:
        try:
            self._browser().close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None

    def send(self, thread_id: str, text: str, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[DRY-RUN -> {thread_id}] {text}")
            return
        ad_id, _, peer_id = thread_id.partition(":")
        try:
            self._xmpp_send(peer_id, ad_id, text)
        except Exception as e:
            print(f"XMPP send failed ({str(e)[:120]}), trying browser fallback")
            self._browser_send(thread_id, text)
