"""Text that leaves the machine never says which tool wrote it.

The pattern is aimed at authorship, not at names: a review of a PR about an
editor plugin may name the editor, but no review says what generated it, who
co-authored it, or that a model reviewed it."""

import re

PATTERN = re.compile(
    r"co-authored-by|generated (?:with|by)|ai[- ]generated|noreply@anthropic|"
    "\U0001F916|"
    r"\b(?:by|from|with|via|using) (?:claude|codex|chatgpt|gpt-\d|an? (?:ai|llm))\b|"
    r"\b(?:claude|codex|chatgpt)(?: code)? (?:wrote|generated|authored|reviewed)\b",
    re.I)


def attributed(text):
    return bool(text and PATTERN.search(text))
