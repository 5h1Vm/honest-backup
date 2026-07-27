import time

import requests

from . import config as cf_config

ENV = cf_config.ENV

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


def headers():
    return {
        "Authorization": f"Bearer {ENV['CLOUDFLARE_API_TOKEN']}",
        "Content-Type": "application/json"
    }


def get(url, params=None):
    """Single GET with retry on throttling and transient failures."""
    last_error = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            r = requests.get(
                url, headers=headers(), params=params, timeout=90
            )
        except requests.RequestException as e:
            last_error = e
            time.sleep(2 ** attempt)
            continue

        if r.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
            retry_after = r.headers.get("Retry-After")
            try:
                wait = int(retry_after) if retry_after else 2 ** attempt
            except ValueError:
                wait = 2 ** attempt
            time.sleep(min(wait, 60))
            last_error = requests.HTTPError(f"{r.status_code} from {url}")
            continue

        r.raise_for_status()
        return r.json()

    raise last_error


def get_all(url, params=None, per_page=1000):
    """Follow Cloudflare's pagination to the end of a collection.

    The previous collector fetched only the first page, which silently capped
    every dataset — audit logs stopped at 100 entries. Returns the same
    envelope shape as get(), with every page's results concatenated.
    """
    base_params = dict(params or {})
    params = dict(base_params)
    params["per_page"] = per_page
    page = 1
    results = []
    envelope = None

    while True:
        params["page"] = page
        try:
            body = get(url, params=params)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            # Some endpoints (rulesets, page rules, device lists) reject
            # pagination parameters outright. Fall back to an unpaginated
            # request rather than losing the dataset.
            if status == 400 and page == 1:
                return get(url, params=base_params or None)
            raise

        if envelope is None:
            envelope = {k: v for k, v in body.items() if k != "result"}

        chunk = body.get("result")
        if chunk is None:
            # Endpoint returns a single object rather than a collection.
            return body
        if isinstance(chunk, dict):
            return body

        results.extend(chunk)

        info = body.get("result_info") or {}
        total_pages = info.get("total_pages")
        if total_pages is not None:
            if page >= total_pages:
                break
        else:
            # No pagination metadata: stop when a short page arrives.
            if len(chunk) < per_page:
                break

        page += 1
        if page > 1000:  # hard stop against a runaway loop
            break

    envelope = envelope or {}
    envelope["result"] = results
    envelope["result_info"] = {
        "collected": len(results),
        "pages_fetched": page,
    }
    return envelope
