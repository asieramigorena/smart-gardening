"""Shared HTTP GET with retries for transient failures and rate limits."""
from __future__ import annotations

import time

import requests

_session = requests.Session()
_RETRY_STATUS = {429, 500, 502, 503, 504}


def get(url: str, *, params=None, headers=None, timeout: float = 60, retries: int = 4) -> requests.Response:
    for attempt in range(retries + 1):
        try:
            resp = _session.get(url, params=params, headers=headers, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == retries:
                raise
            time.sleep(2**attempt)
            continue
        if resp.status_code in _RETRY_STATUS and attempt < retries:
            try:
                wait = float(resp.headers.get("Retry-After", 2**attempt))
            except ValueError:
                wait = 2**attempt
            time.sleep(min(wait, 60))
            continue
        return resp
    raise RuntimeError("unreachable")
