"""HTTP download with an on-disk cache under data/raw/. Only valid files are cached."""
import sys
import urllib.error
import urllib.request

from . import config


_warned_blocked = False


def get(url, cache_name=None, validate=None, retries=3):
    """Return response text, or None if the resource doesn't exist / fails `validate(text)`.

    cache_name: path relative to data/raw/. If cached, the network is skipped.
    validate:   callable(text)->bool; a failing response is treated as missing and NOT cached.
    """
    path = config.RAW_DIR / cache_name if cache_name else None
    if path and path.exists():
        return path.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    text = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as e:
            if e.code == 403:
                global _warned_blocked
                if not _warned_blocked:
                    _warned_blocked = True
                    print("\n  WARNING: got HTTP 403 (likely rate-limited by the source's WAF). "
                          "Cached data still loads; re-run later to fetch the rest.", file=sys.stderr)
                return None
            if e.code in (404, 301, 302):
                return None
        except (urllib.error.URLError, TimeoutError):
            pass
    if text is None or (validate and not validate(text)):
        return None
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return text
