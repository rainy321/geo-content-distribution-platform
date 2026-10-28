"""Open fixed search portal links in the user's normal default browser."""
from __future__ import annotations

import logging
import os
import webbrowser

from services.geo_search_onboarding import PROVIDERS


_PURPOSE_FIELDS = {'entry': 'entry_url', 'help': 'help_url', 'submit': 'submission_url'}
_PORTALS = {item['id']: {purpose: item[field] for purpose, field in _PURPOSE_FIELDS.items()}
            for item in PROVIDERS}


def open_search_portal(provider, purpose):
    # The bridge accepts identifiers only. Never pass a supplied URL, path,
    # command, profile or browser argument to the OS.
    if (not isinstance(provider, str) or provider not in _PORTALS
            or not isinstance(purpose, str) or purpose not in _PURPOSE_FIELDS):
        return {'launch_requested': False, 'reason': 'invalid_search_portal'}
    url = _PORTALS[provider][purpose]
    try:
        if os.name == 'nt':
            os.startfile(url, 'open')
        elif not webbrowser.open_new_tab(url):
            return {'launch_requested': False, 'reason': 'browser_request_rejected'}
    except (OSError, webbrowser.Error):
        logging.exception('Default browser launch request failed for %s/%s', provider, purpose)
        return {'launch_requested': False, 'reason': 'browser_request_rejected'}
    except Exception:
        # Keep native details in the desktop log once; bridge callers receive
        # only a fixed reason code, including for unexpected opener failures.
        logging.exception('Default browser launch request was not confirmed for %s/%s', provider, purpose)
        return {'launch_requested': False, 'reason': 'browser_request_unconfirmed'}
    # Shell acceptance cannot prove that a page is visible, logged in or ready.
    return {'launch_requested': True, 'provider': provider, 'purpose': purpose, 'url': url}
