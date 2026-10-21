"""Conservative URL identities for source/platform recognition, without I/O.

This is not a general research-URL validator or a network/SSRF safety check.
Public DNS syntax does not prove that a domain exists or resolves publicly.
Platform recognition must still match an explicit hostname and content path.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, urlsplit


_HTTPS_URL = re.compile(r'https://([^/?#]+)(.*)', re.IGNORECASE | re.ASCII)
_AUTHORITY = re.compile(r'([a-z0-9.-]+)(?::([0-9]+))?', re.IGNORECASE | re.ASCII)
_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', re.ASCII)
_TLD = re.compile(r'(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})', re.ASCII)
_BAD_ESCAPE = re.compile(r'%(?![a-f0-9]{2})', re.IGNORECASE | re.ASCII)
_PATH_DELIMITER_ESCAPE = re.compile(r'%(?:2f|5c|3f|23|25)', re.IGNORECASE | re.ASCII)
_ESCAPE = re.compile(r'%([a-f0-9]{2})', re.IGNORECASE | re.ASCII)
_UNRESERVED = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~')
# IANA special-use names plus common private-network suffixes are not article
# platform identities. This finite list is deliberately not a public-suffix list.
# https://www.iana.org/assignments/special-use-domain-names/
_NONPUBLIC_SUFFIXES = frozenset({
    'localhost', 'local', 'localdomain', 'internal', 'invalid', 'test', 'example',
    'onion', 'alt', 'arpa', 'home', 'lan', 'corp', 'intranet',
})


def _unsafe_character(value: str) -> bool:
    return any(char == '\\' or char.isspace()
               or unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} for char in value)


def _public_domain(host: str) -> bool:
    labels = host.split('.')
    if (len(host) > 253 or len(labels) < 2 or not _TLD.fullmatch(labels[-1])
            or labels[-1] in _NONPUBLIC_SUFFIXES
            or any(not _LABEL.fullmatch(label) for label in labels)):
        return False
    # Reject invalid ASCII IDN labels rather than guessing Unicode/IDNA mappings.
    try:
        for label in labels:
            if label.startswith('xn--'):
                label.encode('ascii').decode('idna')
    except UnicodeError:
        return False
    return True


def trusted_source_url(value: object) -> str | None:
    """Return a stable HTTPS identity URL, or None for ambiguous/private input.

    Only syntactically public ASCII DNS names (including valid A-labels) are
    accepted; IP literals, numeric IPv4 spellings, local/special suffixes,
    credentials, non-443 ports, whitespace, controls and backslashes are refused.
    Raw or once-decoded dot path segments, encoded path/component separators,
    encoded percent signs, malformed escapes and invalid UTF-8 are refused too.
    HTTPS browser parsing treats backslashes and encoded dot segments specially:
    https://url.spec.whatwg.org/#host-state and #path-state.

    The return value lowercases scheme/hostname, removes a single DNS root dot
    and explicit default port, adds '/' for an empty path, and decodes only ASCII
    unreserved path escapes. Query and fragment text, including empty delimiters
    and their escapes, are retained exactly. Nothing is navigated or resolved.
    Returning None does not mean an ordinary research URL must be discarded.
    """
    if not isinstance(value, str) or _unsafe_character(value):
        return None
    match = _HTTPS_URL.fullmatch(value)
    if not match:
        return None
    authority, tail = match.groups()
    identity = _AUTHORITY.fullmatch(authority)
    if not identity:
        return None
    host, port = identity.groups()
    host = host.lower()
    if host.endswith('.'):
        host = host[:-1]
    if not _public_domain(host) or port is not None and port != '443':
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None:
            return None
        if parsed.port not in (None, 443):
            return None
        path = parsed.path
        if _BAD_ESCAPE.search(path) or _PATH_DELIMITER_ESCAPE.search(path):
            return None
        decoded = unquote(path, encoding='utf-8', errors='strict')
        if _unsafe_character(decoded) or any(segment in {'.', '..'} for segment in decoded.split('/')):
            return None
    except (ValueError, UnicodeError):
        return None

    def unreserved(match):
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else match.group(0)

    normalized_path = _ESCAPE.sub(unreserved, path)
    # Slice the original suffix rather than urlunsplit: it would erase empty ?/#
    # delimiters, and query/fragment data must not become path or authority input.
    suffix = tail[len(path):]
    return 'https://' + host + (normalized_path or '/') + suffix
