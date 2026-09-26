"""A client for brain, the shared memory store.

Every other client of brain — ChatGPT, Claude, brain-chat, life-rpg — writes
through POST /ingest and lets brain's own model decide how a note gets
organised (type, tags, whether it's a new fact or an update to an existing
one, expiry). This app is a client the same way: it holds a bearer token and
nothing else, and never hand-builds a Fact itself.

Where the request goes
-----------------------
To brain, and only there. BRAIN_API_TOKEN is this app's own copy of the same
single static token brain-chat and life-rpg each already hold — brain has no
per-client token issuance the way llm-gateway does.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class BrainNotConfigured(Exception):
    """No URL/token configured, or brain rejected this app's token."""


class BrainError(Exception):
    """The call failed for some other reason."""


def _config():
    url = (getattr(settings, 'BRAIN_API_URL', '') or '').strip()
    token = (getattr(settings, 'BRAIN_API_TOKEN', '') or '').strip()
    if not (url and token):
        raise BrainNotConfigured(
            'No brain memory store is configured. Set BRAIN_API_URL and '
            'BRAIN_API_TOKEN.'
        )
    return url.rstrip('/'), token


def ingest(text, source='toolbox', hint=None, timeout=15):
    """Write a note to brain, letting it decide how to organise it.

    Never raises anything but BrainNotConfigured/BrainError — a memory write
    is a bonus on top of whatever the caller was already doing (e.g. sending
    a weekly brief), never something worth failing that work over. Callers
    are expected to catch both.
    """
    url, token = _config()
    payload = {'text': text, 'source': source}
    if hint:
        payload['hint'] = hint

    try:
        response = requests.post(
            f'{url}/ingest', json=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise BrainError(f'Could not reach brain: {exc}') from exc

    if response.status_code == 401:
        raise BrainNotConfigured("brain rejected this app's token. Check BRAIN_API_TOKEN.")
    if not response.ok:
        raise BrainError(f'brain returned {response.status_code}: {response.text[:300]}')

    return response.json()
