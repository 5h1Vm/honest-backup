import time
from pathlib import Path

import requests

from msal import ConfidentialClientApplication

from .secrets import load_env

# Statuses worth retrying: throttling and transient service errors.
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CERT_KEY = PROJECT_ROOT / "config" / "keys" / "sharepoint-app.key"
CERT_PUB = PROJECT_ROOT / "config" / "keys" / "sharepoint-app.cer"


def certificate_available():
    """True when a certificate is on disk for SharePoint app-only auth."""
    return CERT_KEY.exists() and CERT_PUB.exists()


def _certificate_credential():
    """Build the MSAL credential dict from the certificate on disk."""
    import hashlib

    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert = x509.load_pem_x509_certificate(CERT_PUB.read_bytes())
    thumbprint = hashlib.sha1(cert.public_bytes(serialization.Encoding.DER)).hexdigest()

    return {
        "private_key": CERT_KEY.read_text(),
        "thumbprint": thumbprint,
        "public_certificate": CERT_PUB.read_text(),
    }


def get_token(resource="https://graph.microsoft.com/.default", use_certificate=False):
    """Acquire an app-only token.

    Pass a different resource scope to reach APIs outside Graph (for example
    the Office 365 Management API).

    SharePoint's REST API rejects tokens obtained with a client secret
    ("Unsupported app only token") — it only accepts certificate-based
    app-only auth. Set use_certificate=True for those calls.
    """

    env = load_env()

    if use_certificate:
        if not certificate_available():
            raise RuntimeError(
                "SharePoint app-only access needs a certificate. Expected "
                f"{CERT_KEY.name} and {CERT_PUB.name} in config/keys/."
            )
        credential = _certificate_credential()
    else:
        credential = env["CLIENT_SECRET"]

    app = ConfidentialClientApplication(
        env["CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{env['TENANT_ID']}",
        client_credential=credential,
    )

    token = app.acquire_token_for_client(scopes=[resource])

    if "access_token" not in token:
        raise Exception(token)

    return token["access_token"]


def _request(method, url, headers, json_body=None, timeout=120):
    """Issue a request, retrying throttled and transient failures."""

    last_error = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            r = requests.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=timeout,
            )
        except requests.RequestException as e:
            last_error = e
            time.sleep(2**attempt)
            continue

        if r.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
            # Honour Retry-After when the service sends one.
            delay = r.headers.get("Retry-After")
            try:
                wait = int(delay) if delay else 2**attempt
            except ValueError:
                wait = 2**attempt
            time.sleep(min(wait, 60))
            last_error = requests.HTTPError(f"{r.status_code} from {url}", response=r)
            continue

        r.raise_for_status()
        return r

    raise last_error


def graph_paginated_get(url, headers):
    results = []

    while url:
        r = _request("GET", url, headers)
        data = r.json()
        value = data.get("value")
        if value is None:
            # A single object rather than a collection.
            data.pop("@odata.context", None)
            return [data]

        results.extend(value)
        url = data.get("@odata.nextLink")

    return results


def graph_get(url, headers):
    """Single GET returning the decoded body."""

    return _request("GET", url, headers).json()


def graph_post(url, headers, body):
    """POST returning the decoded body (used by advanced hunting)."""

    return _request("POST", url, headers, json_body=body).json()


def graph_download(url, headers, destination, timeout=300):
    """Stream a binary file to disk. Returns bytes written."""

    destination.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(url, headers=headers, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        written = 0
        with open(destination, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    written += len(chunk)

    return written


def graph_delta_get(url, headers, delta_link=None):
    """
    Perform a delta query.
    If delta_link is provided, it is used as the request URL (the delta link from previous call).
    Otherwise, the initial request is made with ?$delta=true.
    Returns (items, new_delta_link) where new_delta_link is the @odata.deltaLink from the final response.
    """
    if delta_link:
        request_url = delta_link
    else:
        # Append $delta=true
        separator = "&" if "?" in url else "?"
        request_url = f"{url}{separator}$delta=true"

    items = []
    new_delta_link = None

    while request_url:
        r = _request("GET", request_url, headers)
        data = r.json()
        items.extend(data.get("value", []))

        # If there is a next page, continue
        request_url = data.get("@odata.nextLink")
        # If no next page, this is the last response; capture deltaLink if present
        if not request_url:
            new_delta_link = data.get("@odata.deltaLink")

    return items, new_delta_link
