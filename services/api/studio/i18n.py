"""English for the text the backend sends to the interface.

Messages are written in Vietnamese where they are raised or stored. When the interface runs in English it sends the
header `X-Studio-Lang: en`, and every JSON response passes through `localise`, which looks each string up in
`i18n_en.MESSAGES`. Text the operator typed (names, screenplay, prompts…) sits under the keys in `OPERATOR_TEXT` and is
never touched. Files written for other tools (timelines, subtitles, the Resolve guide) are not affected.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from .i18n_en import MESSAGES

VIETNAMESE = re.compile("[À-ÖØ-öø-ɏḀ-ỿ]")
SLOT = re.compile(r"\{(\d+)\}")
# Lists of reasons reach the interface joined in one of these ways.
JOINERS = ("\n", "; ", " · ", ", ")
# Response fields that hold what the operator wrote, a name, or a path. Fields such as `note` and `label` carry
# both kinds of text in different answers; they are looked up, and only an exact system message changes.
OPERATOR_TEXT = frozenset({
    "name", "names", "display_name", "aliases", "entity", "character_name", "characters", "speaker", "voice_name", "scenes",
    "heading", "summary", "description", "description_en", "action", "camera_note", "text", "text_en", "spoken_text",
    "spoken_override", "prompt", "prompt_en", "prompt_override", "prompt_description", "default_prompt", "styled_prompt",
    "sample_prompt", "style_phrases", "banned_words", "lut_name", "keep", "ai_change", "tag", "tags", "labels", "motion_tags",
    "vfx_tags", "detail_json", "root", "path", "relative_path", "editor_path", "project_path", "ue_project_path",
    "current_ue_project_path", "backup_parent", "restore_parent", "bundle", "ffmpeg_path", "ffprobe_path", "detected_path",
    "output_path", "selected_path", "folder", "folders",
})


def _compile():
    exact, patterns = {}, []
    for source, english in MESSAGES.items():
        if not SLOT.search(source):
            exact[source] = english
            continue
        pieces = SLOT.split(source)
        literal = sum(len(piece) for piece in pieces[::2])
        regex = "".join(re.escape(piece) if index % 2 == 0 else "(.+?)" for index, piece in enumerate(pieces))
        patterns.append((literal, re.compile(regex, re.S), [int(piece) for piece in pieces[1::2]], english))
    # The template with the most fixed text is tried first, so a general one never shadows a specific one.
    patterns.sort(key=lambda item: -item[0])
    return exact, [item[1:] for item in patterns]


EXACT, PATTERNS = _compile()


def _translate(text: str, depth: int) -> str:
    core = text.strip()
    if not core or not VIETNAMESE.search(core):
        return text
    start = len(text) - len(text.lstrip())
    lead, trail = text[:start], text[start + len(core):]
    found = _lookup(core, depth)
    if found is None:
        return text
    # A sentence that began with a capital still does when an inserted value now comes first.
    if core[0].isupper() and found[:1].islower():
        found = found[0].upper() + found[1:]
    return lead + found + trail


def _lookup(core: str, depth: int) -> str | None:
    found = EXACT.get(core)
    if found is not None or depth >= 4:
        return found
    for regex, order, english in PATTERNS:
        match = regex.fullmatch(core)
        if match:
            values = [""] * (max(order) + 1)
            for slot, group in zip(order, match.groups()):
                values[slot] = _translate(group, depth + 1)
            return english.format(*values)
    for joiner in JOINERS:
        if joiner in core:
            parts = core.split(joiner)
            done = [_translate(part, depth + 1) for part in parts]
            if done != parts:
                return joiner.join(done)
    return None


@lru_cache(maxsize=8192)
def translate(text: str) -> str:
    """The English for a message, or the text itself when it is not one of ours."""
    return _translate(text, 0)


def localise(value, operator: bool = False):
    """A JSON value with every backend message in English; operator text is left as written."""
    if isinstance(value, str):
        return value if operator or not VIETNAMESE.search(value) else translate(value)
    if isinstance(value, list):
        return [localise(item, operator) for item in value]
    if isinstance(value, dict):
        # A protected field shields its own strings; objects inside it are read field by field again.
        return {key: localise(item, key in OPERATOR_TEXT) for key, item in value.items()}
    return value


class EnglishResponses:
    """ASGI middleware: JSON responses in English for requests that carry `X-Studio-Lang: en`."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or dict(scope["headers"]).get(b"x-studio-lang") != b"en":
            await self.app(scope, receive, send)
            return
        start, chunks, untouched = None, [], False

        async def relay(message):
            nonlocal start, untouched
            if message["type"] == "http.response.start":
                # Streams and files go straight through; only JSON bodies are held back to be rewritten.
                untouched = not dict(message["headers"]).get(b"content-type", b"").startswith(b"application/json")
                if untouched:
                    await send(message)
                else:
                    start = message
                return
            if untouched:
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(chunks)
            try:
                body = json.dumps(localise(json.loads(body)), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except ValueError:
                pass
            headers = [(key, item) for key, item in start["headers"] if key.lower() != b"content-length"]
            await send({**start, "headers": headers + [(b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, relay)
