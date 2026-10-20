"""Hardened stdlib XML parsing for untrusted public documents (feeds, Form 4).

``defusedxml`` is not a dependency, so we refuse any document carrying a DTD (``<!DOCTYPE``) or entity
declaration before handing it to ``xml.etree``. That blocks entity-expansion ("billion laughs") and
external-entity tricks; legitimate RSS/Atom/ownershipDocument XML never needs a DTD.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

MAX_XML_BYTES = 8 * 1024 * 1024
_DANGEROUS = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class UnsafeXmlError(ValueError):
    """Document refused before parsing (DTD/entity declarations, oversize, binary junk)."""


def reject_dangerous_xml(content: bytes) -> bytes:
    if len(content) > MAX_XML_BYTES:
        raise UnsafeXmlError(f"XML document too large ({len(content)} bytes > {MAX_XML_BYTES})")
    if _DANGEROUS.search(content):
        raise UnsafeXmlError("XML document declares a DTD/entity; refused (possible entity expansion)")
    return content


def parse_xml(content: bytes | str) -> ET.Element:
    """Parse untrusted XML into an Element, raising ``ValueError`` subclasses on any problem."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    reject_dangerous_xml(raw)
    try:
        return ET.fromstring(raw)  # noqa: S314 - DTD/entity declarations rejected above
    except ET.ParseError as exc:
        raise ValueError(f"malformed XML: {exc}") from exc


def local_name(tag: str) -> str:
    """Strip an ``{namespace}`` prefix from an element tag."""
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def child(elem: ET.Element | None, name: str) -> ET.Element | None:
    """First direct child whose local name matches (namespace-agnostic)."""
    if elem is None:
        return None
    for c in elem:
        if local_name(c.tag) == name:
            return c
    return None


def children(elem: ET.Element | None, name: str) -> list[ET.Element]:
    if elem is None:
        return []
    return [c for c in elem if local_name(c.tag) == name]


def text_of(elem: ET.Element | None) -> str | None:
    """Concatenated text content (incl. descendants), stripped; None when empty."""
    if elem is None:
        return None
    value = "".join(elem.itertext()).strip()
    return value or None


def path_text(elem: ET.Element | None, *names: str) -> str | None:
    """Text at a namespace-agnostic child path, e.g. ``path_text(tx, 'transactionDate', 'value')``."""
    node = elem
    for name in names:
        node = child(node, name)
        if node is None:
            return None
    return text_of(node)


__all__ = [
    "UnsafeXmlError",
    "child",
    "children",
    "local_name",
    "parse_xml",
    "path_text",
    "reject_dangerous_xml",
    "text_of",
]
