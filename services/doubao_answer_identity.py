"""Exact identity proofs for history pages which omit separate progress bodies."""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser


class _AnswerBodies(HTMLParser):
    _void = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.bodies, self.stack, self.valid = [], [], True
        self.feed(source)
        self.close()
        self.valid = self.valid and not self.stack

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self.stack:
            self.bodies.append({'tag': tag, 'attrs': attrs, 'text': [], 'plain': [], 'tags': set()})
        body = self.bodies[-1]
        body['tags'].add(tag)
        if tag == 'br':
            body['plain'].append('\n')
        if tag not in self._void:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.valid = False
            return
        if tag in {'div', 'p'}:
            self.bodies[-1]['plain'].append('\n')
        self.stack.pop()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.handle_endtag(tag)

    def handle_data(self, value):
        if not self.stack:
            if value.strip():
                self.valid = False
            return
        self.bodies[-1]['text'].append(value)
        self.bodies[-1]['plain'].append(value)


def prove_progress_body_elision(original_response: str, original_snapshot: dict, current_snapshot: dict) -> dict | None:
    """No suffix heuristic: require verified separate DOM bodies and exact bytes.

    Only simple short progress nodes preceding the original final body may
    disappear. Final node text must match, and reconstructing the original
    response from those nodes plus the current final answer must be byte-exact.
    The caller must authenticate original_snapshot against its evidence hash.
    """
    old_dom, new_dom = original_snapshot.get('answer_dom'), current_snapshot.get('answer_dom')
    if (not isinstance(old_dom, str) or not isinstance(new_dom, str)
            or original_snapshot.get('answer') != original_response):
        return None
    old, new = _AnswerBodies(old_dom), _AnswerBodies(new_dom)
    if not old.valid or not new.valid or not 2 <= len(old.bodies) <= 7 or len(new.bodies) != 1:
        return None
    for body in [*old.bodies, *new.bodies]:
        if (body['tag'] != 'div' or body['attrs'].get('data-testid') not in {'message_text_content', 'message-content'}
                or body['attrs'].get('data-streaming') != 'false'):
            return None
    if ''.join(old.bodies[-1]['text']).strip() != ''.join(new.bodies[0]['text']).strip():
        return None
    prefixes = []
    for body in old.bodies[:-1]:
        text = ''.join(body['plain']).strip()
        if (not 1 <= len(text) <= 600 or not body['tags'] <= {'div', 'span', 'p', 'br', 'strong', 'em', 'b', 'i'}
                or not re.match(r'^(?:我(?:来|先).{0,70}(?:调研|检索|核实|核验)|继续(?:并行)?(?:核实|核验|检索|查)|再(?:补|查|核))', text)):
            return None
        prefixes.append(text)
    current_answer = current_snapshot.get('answer')
    if (not isinstance(current_answer, str) or not current_answer
            or '\n\n'.join([*prefixes, current_answer]) != original_response):
        return None
    return {'answer_identity_method': 'exact_final_body_with_dom_proven_progress_prefix',
            'omitted_progress_body_count': len(prefixes),
            'current_answer_sha256': hashlib.sha256(current_answer.encode('utf-8')).hexdigest(),
            'final_body_sha256': hashlib.sha256(current_answer.encode('utf-8')).hexdigest()}
