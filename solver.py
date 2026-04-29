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
BLIISH_MAGIC_LINK_URL = "https://bliish.com/api/v1/auth/magic-link"
GUERRILLA_BASE_URL = "https://api.guerrillamail.com/ajax.php"
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


def _guerrilla_request(
    method: str,
    path: str,
    payload: Optional[dict] = None,
    token: Optional[str] = None,
    timeout: int = 30,
) -> dict:
    params = {"f": path, "ip": "127.0.0.1", "agent": "Mozilla_foo_bar"}
    if token:
        params["sid_token"] = token
    if payload:
        params.update(payload)

    url = GUERRILLA_BASE_URL + "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        data=None,
        headers={"User-Agent": "Mozilla_foo_bar"},
        method=method,
    )

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
        detail = error_obj.get("message") or raw or str(exc)
        raise RuntimeError(
            f"GuerrillaMail {method} {path} failed ({exc.code}): {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GuerrillaMail request failed: {exc.reason}") from exc


def _random_mail_local_part() -> str:
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    return f"bliish{suffix}"


def create_temp_mail_account(
    address: Optional[str] = None,
    password: Optional[str] = None,
    timeout: int = 30,
) -> dict:
    _debug("Step 1/6: creating temporary GuerrillaMail account")
    if address and "@" not in address:
        raise ValueError("address must include '@' when provided")

    if address:
        local_part = address.split("@")[0]
        result = _guerrilla_request(
            "POST",
            "set_email_user",
            payload={"email_user": local_part},
            timeout=timeout,
        )
    else:
        result = _guerrilla_request(
            "POST",
            "get_email_address",
            timeout=timeout,
        )

    email = result.get("email_addr")
    token = result.get("sid_token")

    if not email:
        raise RuntimeError(f"Failed to create GuerrillaMail address: {result}")

    return {
        "address": email,
        "password": "",
        "token": token,
    }


def _unwrap_redirect_url(url: str) -> str:
    """
    If the URL is a redirect/tracker wrapper (e.g. click.mailgun.com?redirect=...),
    extract and return the real inner destination URL. Otherwise return as-is.
    """
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
    """
    Strip whitespace and verify the URL is a navigable http/https URL.
    Raises ValueError if not.
    """
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


def wait_for_verification_link(
    mail_token: str,
    host_hint: str = "bliish.com",
    timeout: int = 180,
    poll_interval: int = 5,
) -> str:
    _debug("Step 4/6: waiting for verification email")
    deadline = time.time() + timeout
    seen = set()
    last_count = 0

    while time.time() < deadline:
        _debug("Polling GuerrillaMail inbox for new messages")
        box = _guerrilla_request("GET", "get_email_list", payload={"offset": "0"}, token=mail_token, timeout=30)
        messages = box.get("list", [])
        current_count = len(messages)

        if current_count > last_count:
            for msg in messages[:current_count]:
                msg_id = msg.get("mail_id")
                if not msg_id or msg_id in seen:
                    continue
                seen.add(msg_id)
                full = _guerrilla_request("GET", "fetch_email", payload={"email_id": msg_id}, token=mail_token, timeout=30)
                urls = _extract_message_urls(full)
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

            last_count = current_count

        time.sleep(poll_interval)

    raise TimeoutError(f"No verification link received within {timeout}s")


async def _open_verification_and_click_button(url: str, timeout: int = 45) -> dict:
    import nodriver as uc

    # Validate URL before even starting the browser
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Invalid verification URL passed to browser: {url!r}")

    # If the URL contains a confirmation_url param, navigate directly to that
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
        # Extract the confirmation_url param and navigate directly to it -
        # this is the real Supabase verify endpoint that sets the auth session.
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "confirmation_url" in qs:
            direct_url = unquote(qs["confirmation_url"][0]).strip()
            _debug(f"Step 5/6: navigating directly to confirmation_url: {direct_url!r}")
        else:
            direct_url = url
            _debug(f"Step 5/6: no confirmation_url param, navigating to: {direct_url!r}")

        page = await browser.get(direct_url)

        # Poll until page settles on email-link?code=... (after auth.bliish.com redirect)
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

        # If we got the code page, build and navigate directly to the API callback URL
        if callback_url:
            cb_parsed = urlparse(callback_url)
            cb_qs = parse_qs(cb_parsed.query)
            code = cb_qs.get("code", [""])[0]
            if code:
                api_callback = f"https://bliish.com/api/v1/auth/callback?code={code}&token_hash=&type=&next=/feed"
                _debug(f"Navigating directly to API callback: {api_callback!r}")
                page = await browser.get(api_callback)
                await asyncio.sleep(3.0)
                # Skip button click, jump straight to redirect wait
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

        # Fallback: click whatever button is on the page
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

        # Wait for final redirect to /feed or any non-auth bliish page
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


def create_guerrilla_account_and_verify(
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
    intent: str = "signup",
    timeout: int = 45,
) -> dict:
    _debug("Step 3/6: sending Bliish magic-link API request")
    payload = {
        "email": email,
        "turnstileToken": turnstile_token,
        "intent": intent,
    }
    req = urllib.request.Request(
        BLIISH_MAGIC_LINK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://bliish.com",
            "Referer": DEFAULT_SITEURL,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            body = json.loads(raw) if raw else {}
            return {
                "status": getattr(resp, "status", 200),
                "body": body,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"raw": raw}
        raise RuntimeError(
            f"Bliish magic-link request failed ({exc.code}): {body}"
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
        "guerrilla_token": mail["token"],
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
        mail_token=result["guerrilla_token"],
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

        # Inject widget into the live page DOM
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

        # Give Turnstile time to load and potentially auto-complete (invisible mode)
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
                # Widget is fixed at top:20px left:20px
                cx = 20 + 28 + random.uniform(-3, 3)
                cy = 20 + 32 + random.uniform(-3, 3)
                print(f"[solver] iframe not in DOM, clicking fixed position ({cx:.0f}, {cy:.0f})")
            await page.mouse_move(cx - 80, cy - 20)
            await asyncio.sleep(random.uniform(0.15, 0.25))
            await page.mouse_move(cx, cy)
            await asyncio.sleep(random.uniform(0.08, 0.15))
            await page.mouse_click(cx, cy)

        # Check if already auto-solved (invisible widget)
        token = await get_token()
        if token:
            _debug("Turnstile solved automatically")
            return token

        # Wait up to 10s for the visible checkbox iframe to appear
        rect = None
        for _ in range(20):
            rect = await get_cf_iframe_rect()
            if rect:
                break
            await asyncio.sleep(0.5)

        # Click loop: click, wait, retry up to 3 times
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
                # After a click, refresh iframe rect in case it moved
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


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "--guerrilla-create":
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

    if len(sys.argv) >= 2 and sys.argv[1] == "--guerrilla-magic-link":
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

    if len(sys.argv) >= 3 and sys.argv[1] == "--guerrilla-wait":
        token = sys.argv[2]
        host_hint = sys.argv[3] if len(sys.argv) >= 4 else "bliish.com"
        timeout = int(sys.argv[4]) if len(sys.argv) >= 5 else 180
        link = wait_for_verification_link(token, host_hint=host_hint, timeout=timeout)
        print(link)
        sys.exit(0)

    if len(sys.argv) >= 2 and sys.argv[1] == "--guerrilla-create-and-verify":
        host_hint = sys.argv[2] if len(sys.argv) >= 3 else "bliish.com"
        timeout = int(sys.argv[3]) if len(sys.argv) >= 4 else 180
        result = create_guerrilla_account_and_verify(host_hint=host_hint, timeout=timeout)
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if len(sys.argv) > 3:
        print(
            "Usage: python solver.py\n"
            "       python solver.py [sitekey] [siteurl]  # token-only mode\n"
            "       python solver.py --guerrilla-create\n"
            "       python solver.py --magic-link <email> <turnstile_token> [intent]\n"
            "       python solver.py --guerrilla-magic-link [intent] [timeout]  # full signup flow\n"
            "       python solver.py --guerrilla-wait <guerrilla_token> [host_hint] [timeout]\n"
            "       python solver.py --guerrilla-create-and-verify [host_hint] [timeout]"
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