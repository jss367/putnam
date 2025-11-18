"""Utilities for tidying OCR-heavy article text."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Iterable, Tuple

logger = logging.getLogger("cleanup")


def _apply_substitutions(text: str, substitutions: Iterable[Tuple[str, str]]) -> str:
    for pattern, repl in substitutions:
        text = re.sub(pattern, repl, text, flags=re.MULTILINE)
    return text


def clean_markdown(markdown: str) -> str:
    """Lightly clean markdown extracted from OCR-heavy pages.

    The cleaner focuses on benign normalizations that are common in scanned
    documents:
    - Normalize unicode so accented letters and punctuation are consistent.
    - Strip non-breaking spaces that behave like stray blanks in output.
    - Remove line-break hyphenation (``word-\nwrap`` -> ``wordwrap``).
    - Convert repeated whitespace to single spaces and tidy blank lines.
    - Replace tildes used as faux hyphens with real hyphens.
    """

    logger.info("Cleaning extracted markdown")

    text = unicodedata.normalize("NFKC", markdown)
    text = text.replace("\ufeff", "")
    text = text.replace("\u00a0", " ")

    substitutions = [
        # Hyphenated line breaks often produced by OCR.
        (r"(?<=\w)-\n(?=\w)", ""),
        # Clean up stray tildes that represent broken hyphens in OCR output.
        (r"~", "-"),
        # Collapse runs of spaces or tabs.
        (r"[\t ]+", " "),
        # Remove extra spaces around newlines.
        (r" *\n *", "\n"),
        # Reduce multiple blank lines to at most two.
        (r"\n{3,}", "\n\n"),
    ]

    cleaned = _apply_substitutions(text, substitutions).strip()
    return cleaned + "\n"
