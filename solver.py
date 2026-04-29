import asyncio
import json
import os
import platform
import random
import re
import string
import subprocess
import time
import urllib.error
import urllib.request
from html import unescape
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote
"""
MADE BY ISMOILOFF. GOOD LUCK HAVE FUN, THIS IS JUST PROJECT, USE IT ON UR OWN RISKS!

"""
DEFAULT_SITEKEY = "0x4AAAAAACjDDNAekcUcF0h5"
DEFAULT_SITEURL = "https://bliish.com/"
BLIISH_MAGIC_LINK_URL = "https://bliish.com/lite/auth?next=/lite/feed"

# mail.tm base URL
MAILTM_BASE_URL = "https://api.mail.tm"

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def _debug(message: str) -> None:
    print(f"[debug] {message}")


def _find_chrome() -> str:
    """Return the Chrome executable path, checking common locations per OS."""
    if os.environ.get("CHROME_PATH"):
        return os.environ["CHROME_PATH"]

    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    else:
        candidates = [
            "/usr/bin/google-chrome-stable",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ]

    for path in candidates:
        if os.path.isfile(path):
            return path

    raise FileNotFoundError(
        "Chrome not found in default locations. "
        "Set the CHROME_PATH environment variable to your Chrome executable."
    )


def _get_profile_dir() -> str:
    """Return a persistent Chrome profile directory for the current OS."""
    if os.environ.get("TS_PROFILE_DIR"):
        return os.environ["TS_PROFILE_DIR"]
    if platform.system() == "Windows":
        base = os.environ.get("TEMP") or os.environ.get("TMP") or r"C:\Temp"
        return os.path.join(base, "ts_profile")
    return "/tmp/ts_profile"


def _start_xvfb_if_needed() -> Optional[subprocess.Popen]:
    """On Linux headless servers, start a virtual display so Chrome can run."""
    if platform.system() != "Linux":
        return None
    if os.environ.get("DISPLAY"):
        return None
    proc = subprocess.Popen(
        ["Xvfb", ":99", "-screen", "0", "1280x900x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.environ["DISPLAY"] = ":99"
    time.sleep(0.5)
    return proc


# ---------------------------------------------------------------------------
# mail.tm helpers
# ---------------------------------------------------------------------------

def _mailtm_request(
    method: str,
    path: str,
    payload: Optional[dict] = None,
    token: Optional[str] = None,
    timeout: int = 30,
) -> dict:
    """Low-level mail.tm REST request."""
    url = f"{MAILTM_BASE_URL}/{path.lstrip('/')}"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            error_obj = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            error_obj = {}
        detail = (
            error_obj.get("hydra:description")
            or error_obj.get("detail")
            or raw
            or str(exc)
        )
        raise RuntimeError(
            f"mail.tm {method} {path} failed ({exc.code}): {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"mail.tm request failed: {exc.reason}") from exc


def _mailtm_get_domains(timeout: int = 30) -> list[str]:
    """Return a list of available mail.tm domains."""
    resp = _mailtm_request("GET", "domains?page=1", timeout=timeout)
    members = resp if isinstance(resp, list) else resp.get("hydra:member", [])
    return [d["domain"] for d in members if d.get("isActive")]


def _random_mail_local_part() -> str:
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    return f"bliish{suffix}"


def create_temp_mail_account(
    address: Optional[str] = None,
    password: Optional[str] = None,
    timeout: int = 30,
) -> dict:
    """Create a mail.tm account and return address, password, token, and account id."""
    _debug("Step 1/6: creating temporary mail.tm account")

    domains = _mailtm_get_domains(timeout=timeout)
    if not domains:
        raise RuntimeError("No active mail.tm domains available")

    if not address:
        address = f"{_random_mail_local_part()}@{domains[0]}"
    elif "@" not in address:
        raise ValueError("address must include '@' when provided")

    if not password:
        password = "".join(
            random.choice(string.ascii_letters + string.digits + "!@#$%^&*")
            for _ in range(16)
        )

    # Register the account
    _mailtm_request(
        "POST",
        "accounts",
        payload={"address": address, "password": password},
        timeout=timeout,
    )

    # Obtain a JWT token
    token_resp = _mailtm_request(
        "POST",
        "token",
        payload={"address": address, "password": password},
        timeout=timeout,
    )
    jwt = token_resp.get("token")
    if not jwt:
        raise RuntimeError(f"mail.tm did not return a token: {token_resp}")

    # Fetch account id
    me = _mailtm_request("GET", "me", token=jwt, timeout=timeout)
    account_id = me.get("id", "")

    _debug(f"mail.tm account created: {address}")
    return {
        "address": address,
        "password": password,
        "token": jwt,          # JWT bearer token for subsequent API calls
        "account_id": account_id,
    }


# ---------------------------------------------------------------------------
# URL helpers (unchanged from original)
# ---------------------------------------------------------------------------

def _unwrap_redirect_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        for param in ("redirect", "url", "link", "target", "next", "u", "to"):
            qs = parse_qs(parsed.query)
            if param in qs:
                inner = unquote(qs[param][0]).strip()
                if inner.startswith(("http://", "https://")):
                    _debug(f"Unwrapped redirect URL via param '{param}': {inner!r}")
                    return inner
    except Exception:
        pass
    return url


def _validate_url(url: str) -> str:
    url = url.strip()
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise ValueError(f"Could not parse URL: {url!r}") from exc
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL has non-http scheme {parsed.scheme!r}: {url!r}")
    if not parsed.netloc:
        raise ValueError(f"URL has no host: {url!r}")
    return url


def _extract_message_urls(message: dict) -> list[str]:
    content_parts = []
    for key in ("subject", "intro", "text"):
        value = message.get(key)
        if isinstance(value, str):
            content_parts.append(value)
    html_part = message.get("html")
    if isinstance(html_part, str):
        content_parts.append(html_part)
    elif isinstance(html_part, list):
        content_parts.extend(v for v in html_part if isinstance(v, str))

    merged = unescape("\n".join(content_parts))
    urls = []
    for url in _URL_RE.findall(merged):
        cleaned = url.rstrip(").,;\"'[]")
        if cleaned not in urls:
            urls.append(cleaned)
    return urls


# ---------------------------------------------------------------------------
# Inbox polling — now using mail.tm
# ---------------------------------------------------------------------------

def wait_for_verification_link(
    mail_token: str,          # JWT bearer token for mail.tm
    host_hint: str = "bliish.com",
    timeout: int = 180,
    poll_interval: int = 5,
) -> str:
    """Poll the mail.tm inbox until a verification link containing host_hint arrives."""
    _debug("Step 4/6: waiting for verification email (mail.tm)")
    deadline = time.time() + timeout
    seen: set = set()

    while time.time() < deadline:
        _debug("Polling mail.tm inbox for new messages")
        try:
            box = _mailtm_request("GET", "messages?page=1", token=mail_token, timeout=30)
        except RuntimeError as exc:
            _debug(f"Inbox poll error: {exc}")
            time.sleep(poll_interval)
            continue

        messages = box if isinstance(box, list) else box.get("hydra:member", [])

        for msg_stub in messages:
            msg_id = msg_stub.get("id")
            if not msg_id or msg_id in seen:
                continue
            seen.add(msg_id)

            # Fetch full message for body
            try:
                full = _mailtm_request("GET", f"messages/{msg_id}", token=mail_token, timeout=30)
            except RuntimeError as exc:
                _debug(f"Could not fetch message {msg_id}: {exc}")
                continue

            # Build content for URL extraction
            content_parts = []
            text_body = full.get("text")
            if isinstance(text_body, str):
                content_parts.append(text_body)
            html_body = full.get("html")
            if isinstance(html_body, str):
                content_parts.append(html_body)
            elif isinstance(html_body, list):
                content_parts.extend(v for v in html_body if isinstance(v, str))

            urls = _extract_message_urls({"text": "\n".join(content_parts)})
            if not urls:
                continue

            if host_hint:
                candidates_found = []
                for url in urls:
                    unwrapped = _unwrap_redirect_url(url)
                    if host_hint.lower() not in unwrapped.lower():
                        if host_hint.lower() not in url.lower():
                            continue
                        unwrapped = url
                    try:
                        validated = _validate_url(unwrapped)
                    except ValueError as exc:
                        _debug(f"Skipping invalid URL {unwrapped!r}: {exc}")
                        continue
                    candidates_found.append(validated)

                if candidates_found:
                    def _url_score(u):
                        p = urlparse(u)
                        return len(p.path.strip("/")) + len(p.query)
                    candidates_found.sort(key=_url_score, reverse=True)
                    chosen = candidates_found[0]
                    _debug(f"Verification link found: {chosen}")
                    return chosen
            else:
                unwrapped = _unwrap_redirect_url(urls[0])
                try:
                    validated = _validate_url(unwrapped)
                except ValueError as exc:
                    _debug(f"Skipping invalid URL {urls[0]!r}: {exc}")
                    continue
                _debug(f"Verification link found: {validated}")
                return validated

        time.sleep(poll_interval)

    raise TimeoutError(f"No verification link received within {timeout}s")


# ---------------------------------------------------------------------------
# Browser verification (unchanged from original)
# ---------------------------------------------------------------------------

async def _open_verification_and_click_button(url: str, timeout: int = 45) -> dict:
    import nodriver as uc

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Invalid verification URL passed to browser: {url!r}")

    try:
        _parsed = urlparse(url)
        _qs = parse_qs(_parsed.query)
        if "confirmation_url" in _qs:
            inner = unquote(_qs["confirmation_url"][0]).strip()
            if inner.startswith(("http://", "https://")):
                _debug(f"Extracted confirmation_url: {inner!r}")
                url = inner
    except Exception:
        pass

    _debug(f"Verification URL to navigate: {url!r}")

    browser = await uc.start(
        browser_executable_path=_find_chrome(),
        headless=False,
        browser_args=[
            "--window-size=1280,900",
            "--window-position=100,100",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            "--profile-directory=Default",
            "--hide-crash-restore-bubble",
            "--suppress-message-center-popups",
        ],
    )
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "confirmation_url" in qs:
            direct_url = unquote(qs["confirmation_url"][0]).strip()
            _debug(f"Step 5/6: navigating directly to confirmation_url: {direct_url!r}")
        else:
            direct_url = url
            _debug(f"Step 5/6: no confirmation_url param, navigating to: {direct_url!r}")

        page = await browser.get(direct_url)

        _debug("Waiting for callback page to load...")
        callback_url = None
        for _ in range(30):
            await asyncio.sleep(0.5)
            try:
                cur = await page.evaluate("window.location.href")
                if isinstance(cur, str) and "email-link" in cur and "code=" in cur:
                    _debug(f"Landed on callback page: {cur!r}")
                    callback_url = cur
                    break
                elif isinstance(cur, str) and cur not in ("about:blank", direct_url, ""):
                    _debug(f"Intermediate URL: {cur!r}")
            except Exception:
                pass

        if callback_url:
            cb_parsed = urlparse(callback_url)
            cb_qs = parse_qs(cb_parsed.query)
            code = cb_qs.get("code", [""])[0]
            if code:
                api_callback = f"https://bliish.com/api/v1/auth/callback?code={code}&token_hash=&type=&next=/feed"
                _debug(f"Navigating directly to API callback: {api_callback!r}")
                page = await browser.get(api_callback)
                await asyncio.sleep(3.0)
                _debug("Step 6/6: waiting for /feed redirect")
                feed_deadline = time.time() + min(timeout, 20)
                final_url = None
                while time.time() < feed_deadline:
                    try:
                        current_url = await page.evaluate("window.location.href")
                        if isinstance(current_url, str):
                            _debug(f"Current URL: {current_url}")
                            if "/feed" in current_url or (
                                "bliish.com" in current_url
                                and "/auth/" not in current_url
                                and "email-link" not in current_url
                            ):
                                final_url = current_url
                                break
                    except Exception:
                        pass
                    await asyncio.sleep(1.0)
                if final_url:
                    _debug(f"Landed on: {final_url}")
                else:
                    _debug("Redirect timeout — may still have succeeded")
                await asyncio.sleep(3.0)
                return {"status": 200, "button": "api_callback", "final_url": final_url or ""}

        await asyncio.sleep(1.5)

        _debug("Step 5/6: clicking confirmation button (fallback)")
        clicked_raw = await page.evaluate("""
            JSON.stringify((() => {
                const isVisible = (el) => {
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                };
                const getLabel = (el) => {
                    if (el instanceof HTMLInputElement) return (el.value || '').trim();
                    return (el.innerText || el.textContent || '').trim();
                };
                const candidates = Array.from(
                    document.querySelectorAll('button, input[type="submit"], [role="button"], a')
                );
                for (const el of candidates) {
                    if (!isVisible(el)) continue;
                    if (el instanceof HTMLButtonElement && el.disabled) continue;
                    if (el instanceof HTMLInputElement && el.disabled) continue;
                    const label = getLabel(el);
                    if (!label && !(el instanceof HTMLInputElement)) continue;
                    el.scrollIntoView({behavior: 'instant', block: 'center', inline: 'center'});
                    el.click();
                    return {clicked: true, label: label || 'submit'};
                }
                return {clicked: false, label: ''};
            })()
        )""")
        try:
            clicked = json.loads(clicked_raw) if isinstance(clicked_raw, str) else clicked_raw
        except (json.JSONDecodeError, TypeError):
            clicked = {}
        label = clicked.get("label", "") if isinstance(clicked, dict) else ""
        if clicked and isinstance(clicked, dict) and clicked.get("clicked"):
            _debug(f"Clicked button: {label!r}")
        else:
            _debug("No button found on callback page - may have auto-redirected")

        _debug("Waiting for post-verification redirect...")
        feed_deadline = time.time() + min(timeout, 30)
        final_url = None
        while time.time() < feed_deadline:
            try:
                current_url = await page.evaluate("window.location.href")
                if isinstance(current_url, str):
                    _debug(f"Current URL: {current_url}")
                    if "/feed" in current_url or (
                        "bliish.com" in current_url
                        and "/auth/" not in current_url
                        and "email-link" not in current_url
                        and "confirmation_url" not in current_url
                    ):
                        final_url = current_url
                        break
            except Exception:
                pass
            await asyncio.sleep(1.0)

        if final_url:
            _debug(f"Step 6/6: landed on {final_url}")
        else:
            _debug("Step 6/6: redirect timeout - verification may still have succeeded")

        await asyncio.sleep(3.0)

        return {
            "status": 200,
            "button": label,
            "final_url": final_url or "",
        }
    finally:
        browser.stop()


def verify_link(url: str, timeout: int = 45) -> dict:
    import warnings

    xvfb = _start_xvfb_if_needed()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return asyncio.run(_open_verification_and_click_button(url, timeout=timeout))
    finally:
        if xvfb:
            xvfb.terminate()


# ---------------------------------------------------------------------------
# High-level flows
# ---------------------------------------------------------------------------

def create_mailtm_account_and_verify(
    host_hint: str = "bliish.com",
    timeout: int = 180,
    poll_interval: int = 5,
) -> dict:
    mail = create_temp_mail_account()
    verification_url = wait_for_verification_link(
        mail_token=mail["token"],
        host_hint=host_hint,
        timeout=timeout,
        poll_interval=poll_interval,
    )
    verification = verify_link(verification_url)
    return {
        **mail,
        "verification_url": verification_url,
        "verification_status": verification["status"],
        "verification_button": verification["button"],
    }


def send_magic_link_request(
    email: str,
    turnstile_token: str,
    intent: str = "magic_link",
    next: str = "/lite/feed",
    timeout: int = 45,
) -> dict:
    _debug("Step 3/6: sending Bliish magic-link API request")
    data = urllib.parse.urlencode({
        "email": email,
        "cf-turnstile-response": turnstile_token,
        "intent": intent,
    }).encode("utf-8")
    req = urllib.request.Request(
        BLIISH_MAGIC_LINK_URL,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Origin": "https://bliish.com",
            "Referer": DEFAULT_SITEURL,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return {
                "status": getattr(resp, "status", 200),
                "body": raw,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"Bliish magic-link request failed ({exc.code}): {raw[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Bliish magic-link request failed: {exc.reason}") from exc


def create_temp_mail_and_request_magic_link(
    sitekey: str = DEFAULT_SITEKEY,
    siteurl: str = DEFAULT_SITEURL,
    intent: str = "signup",
    timeout: int = 45,
) -> dict:
    mail = create_temp_mail_account(timeout=timeout)
    _debug("Step 2/6: solving Turnstile token")
    turnstile_token = solve(sitekey=sitekey, siteurl=siteurl, timeout=timeout)
    request_result = send_magic_link_request(
        email=mail["address"],
        turnstile_token=turnstile_token,
        intent=intent,
        timeout=timeout,
    )
    return {
        "email": mail["address"],
        "mailtm_token": mail["token"],       # renamed from catchmail_token
        "mailtm_password": mail["password"],
        "turnstile_token": turnstile_token,
        "magic_link_response": request_result,
    }


def create_temp_mail_and_register_bliish(
    sitekey: str = DEFAULT_SITEKEY,
    siteurl: str = DEFAULT_SITEURL,
    intent: str = "signup",
    timeout: int = 45,
    verify_timeout: int = 180,
    poll_interval: int = 5,
) -> dict:
    _debug("Starting full Bliish signup flow")
    result = create_temp_mail_and_request_magic_link(
        sitekey=sitekey,
        siteurl=siteurl,
        intent=intent,
        timeout=timeout,
    )
    verification_url = wait_for_verification_link(
        mail_token=result["mailtm_token"],
        host_hint="bliish.com",
        timeout=verify_timeout,
        poll_interval=poll_interval,
    )
    verification = verify_link(verification_url, timeout=timeout)
    return {
        **result,
        "verification_url": verification_url,
        "verification_status": verification["status"],
        "verification_button": verification["button"],
    }


# ---------------------------------------------------------------------------
# Turnstile solver (unchanged from original)
# ---------------------------------------------------------------------------

async def _solve(sitekey: str, siteurl: str, timeout: int) -> str:
    import nodriver as uc

    browser = await uc.start(
        browser_executable_path=_find_chrome(),
        headless=False,
        browser_args=[
            "--window-size=1280,900",
            "--window-position=0,0",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            "--profile-directory=Default",
            "--hide-crash-restore-bubble",
            "--suppress-message-center-popups",
        ],
    )

    try:
        _debug(f"Opening target page: {siteurl}")
        page = await browser.get(siteurl)
        await asyncio.sleep(random.uniform(2.0, 3.0))

        await page.evaluate(f"""
            (() => {{
                if (document.getElementById('_ts_box')) return;
                window._tsToken = null;
                const wrap = document.createElement('div');
                wrap.id = '_ts_box';
                wrap.style = 'position:fixed;top:20px;left:20px;z-index:2147483647;';
                document.body.appendChild(wrap);
                window._tsLoad = function () {{
                    turnstile.render('#_ts_box', {{
                        sitekey: '{sitekey}',
                        callback: function(token) {{ window._tsToken = token; }}
                    }});
                }};
                const s = document.createElement('script');
                s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=_tsLoad&render=explicit';
                s.async = true;
                document.head.appendChild(s);
            }})();
        """)

        await asyncio.sleep(5.0)

        async def get_token() -> Optional[str]:
            return await page.evaluate("""
                (() => {
                    if (window._tsToken) return window._tsToken;
                    const inp = document.querySelector('#_ts_box [name="cf-turnstile-response"]');
                    return (inp && inp.value) ? inp.value : null;
                })()
            """)

        async def get_cf_iframe_rect() -> Optional[dict]:
            raw = await page.evaluate("""
                JSON.stringify((() => {
                    for (const f of document.querySelectorAll('iframe')) {
                        const src = f.src || f.getAttribute('src') || '';
                        if (!src.includes('challenges.cloudflare.com')) continue;
                        const r = f.getBoundingClientRect();
                        if (r.width > 50 && r.height > 20) return {x:r.x, y:r.y, w:r.width, h:r.height};
                    }
                    return null;
                })())
            """)
            if raw and raw != 'null':
                return json.loads(raw)
            return None

        async def do_click(rect: Optional[dict]):
            if rect:
                cx = rect["x"] + 28 + random.uniform(-3, 3)
                cy = rect["y"] + rect["h"] / 2 + random.uniform(-3, 3)
                print(f"[solver] clicking Cloudflare iframe at ({cx:.0f}, {cy:.0f})")
            else:
                cx = 20 + 28 + random.uniform(-3, 3)
                cy = 20 + 32 + random.uniform(-3, 3)
                print(f"[solver] iframe not in DOM, clicking fixed position ({cx:.0f}, {cy:.0f})")
            await page.mouse_move(cx - 80, cy - 20)
            await asyncio.sleep(random.uniform(0.15, 0.25))
            await page.mouse_move(cx, cy)
            await asyncio.sleep(random.uniform(0.08, 0.15))
            await page.mouse_click(cx, cy)

        token = await get_token()
        if token:
            _debug("Turnstile solved automatically")
            return token

        rect = None
        for _ in range(20):
            rect = await get_cf_iframe_rect()
            if rect:
                break
            await asyncio.sleep(0.5)

        deadline = asyncio.get_event_loop().time() + timeout
        click_count = 0
        last_click = 0.0

        while asyncio.get_event_loop().time() < deadline:
            token = await get_token()
            if token:
                break

            now = asyncio.get_event_loop().time()
            if click_count == 0 or (not token and now - last_click > 8):
                if click_count >= 3:
                    await asyncio.sleep(0.3)
                    continue
                await do_click(rect)
                last_click = asyncio.get_event_loop().time()
                click_count += 1
                await asyncio.sleep(1.0)
                rect = await get_cf_iframe_rect() or rect
                continue

            await asyncio.sleep(0.3)

    finally:
        browser.stop()

    if not token:
        raise TimeoutError(f"Turnstile token not obtained within {timeout}s")

    _debug("Turnstile token solved successfully")
    return token


def solve(sitekey: str = DEFAULT_SITEKEY, siteurl: str = DEFAULT_SITEURL, timeout: int = 45) -> str:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return asyncio.run(_solve(sitekey, siteurl, timeout))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "--mailtm-create":
        print(json.dumps(create_temp_mail_account(), indent=2))
        sys.exit(0)

    if len(sys.argv) >= 4 and sys.argv[1] == "--magic-link":
        email = sys.argv[2]
        turnstile_token = sys.argv[3]
        intent = sys.argv[4] if len(sys.argv) >= 5 else "signup"
        result = send_magic_link_request(
            email=email,
            turnstile_token=turnstile_token,
            intent=intent,
        )
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if len(sys.argv) >= 2 and sys.argv[1] == "--mailtm-magic-link":
        intent = sys.argv[2] if len(sys.argv) >= 3 else "signup"
        timeout = int(sys.argv[3]) if len(sys.argv) >= 4 else 45
        xvfb = _start_xvfb_if_needed()
        try:
            result = create_temp_mail_and_register_bliish(
                sitekey=DEFAULT_SITEKEY,
                siteurl=DEFAULT_SITEURL,
                intent=intent,
                timeout=timeout,
            )
            print(json.dumps(result, indent=2))
        finally:
            if xvfb:
                xvfb.terminate()
        sys.exit(0)

    if len(sys.argv) >= 3 and sys.argv[1] == "--mailtm-wait":
        # argv[2] = JWT token, argv[3] = host_hint, argv[4] = timeout
        jwt_token = sys.argv[2]
        host_hint = sys.argv[3] if len(sys.argv) >= 4 else "bliish.com"
        timeout = int(sys.argv[4]) if len(sys.argv) >= 5 else 180
        link = wait_for_verification_link(jwt_token, host_hint=host_hint, timeout=timeout)
        print(link)
        sys.exit(0)

    if len(sys.argv) >= 2 and sys.argv[1] == "--mailtm-create-and-verify":
        host_hint = sys.argv[2] if len(sys.argv) >= 3 else "bliish.com"
        timeout = int(sys.argv[3]) if len(sys.argv) >= 4 else 180
        result = create_mailtm_account_and_verify(host_hint=host_hint, timeout=timeout)
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if len(sys.argv) > 3:
        print(
            "Usage: python solver.py\n"
            "       python solver.py [sitekey] [siteurl]  # token-only mode\n"
            "       python solver.py --mailtm-create\n"
            "       python solver.py --magic-link <email> <turnstile_token> [intent]\n"
            "       python solver.py --mailtm-magic-link [intent] [timeout]  # full signup flow\n"
            "       python solver.py --mailtm-wait <jwt_token> [host_hint] [timeout]\n"
            "       python solver.py --mailtm-create-and-verify [host_hint] [timeout]"
        )
        sys.exit(1)

    if len(sys.argv) == 1:
        xvfb = _start_xvfb_if_needed()
        try:
            result = create_temp_mail_and_register_bliish(
                sitekey=DEFAULT_SITEKEY,
                siteurl=DEFAULT_SITEURL,
                intent="signup",
                timeout=45,
            )
            print(json.dumps(result, indent=2))
        finally:
            if xvfb:
                xvfb.terminate()
        sys.exit(0)

    xvfb = _start_xvfb_if_needed()
    try:
        sitekey = sys.argv[1] if len(sys.argv) >= 2 else DEFAULT_SITEKEY
        siteurl = sys.argv[2] if len(sys.argv) >= 3 else DEFAULT_SITEURL
        token = solve(sitekey, siteurl)
        print(token)
    finally:
        if xvfb:
            xvfb.terminate()
