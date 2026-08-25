"""Convert model markdown into the formatting Google Chat actually renders.

Chat's plain-message syntax is not markdown: it wants `*bold*`, `_italic_` and
`` `code` ``, so a model's `**bold**` and `- ` bullets arrive as literal
punctuation. A classic Chat app can set `textSyntax: "MARKDOWN"` on a card's
textParagraph and skip this, but a plain message has no such field, so this stays
the portable path.
"""

from __future__ import annotations

import re

_CODE_SPAN = re.compile(r"`[^`\n]+`")
_BOLD = re.compile(r"\*\*(?P<text>[^*\n]+)\*\*")
_BOLD_UNDERSCORE = re.compile(r"__(?P<text>[^_\n]+)__")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<text>.+?)\s*#*\s*$")
_BULLET = re.compile(r"^(?P<indent>\s*)[-*+]\s+(?P<text>.+)$")


def _convert_line(line: str) -> str:
    if heading := _HEADING.match(line):
        return f"*{heading.group('text').strip()}*"
    if bullet := _BULLET.match(line):
        return f"{bullet.group('indent')}• {bullet.group('text').strip()}"
    return line


def to_chat_text(markdown: str) -> str:
    """Rewrite markdown emphasis and bullets into Chat's plain-message syntax.

    Code spans are held out and restored so their contents are never rewritten;
    a literal `**` inside backticks is content, not emphasis.
    """
    held: list[str] = []

    def hold(match: re.Match[str]) -> str:
        held.append(match.group(0))
        return f"\x00{len(held) - 1}\x00"

    text = _CODE_SPAN.sub(hold, markdown)
    text = _BOLD.sub(lambda match: f"*{match.group('text')}*", text)
    text = _BOLD_UNDERSCORE.sub(lambda match: f"*{match.group('text')}*", text)
    text = "\n".join(_convert_line(line) for line in text.split("\n"))
    for index, original in enumerate(held):
        text = text.replace(f"\x00{index}\x00", original)
    return text.strip()
