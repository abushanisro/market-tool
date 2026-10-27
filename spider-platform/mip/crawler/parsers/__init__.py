"""Parser registry.

A parser is ``fn(response, ctx) -> Iterable[dict | Follow]`` where yielded dicts carry an
``item_type`` of ``observation`` | ``document`` | ``standard``. Source-specific parsers are
preferred; the generic ones (``html_table``, ``html_css``, ``jsonld``, ``csv_table``,
``pdf_tables``, ``article``, ``rss``) are configured entirely from the source's
``extraction`` block, so new sources need no code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


@dataclass
class Follow:
    url: str
    parser: str | None = None  # None -> same parser as the source
    meta: dict[str, Any] = field(default_factory=dict)
    priority: int = 0


@dataclass
class ParseContext:
    source: dict[str, Any]  # snapshot of the Source row
    options: dict[str, Any]  # source.extraction
    job_id: int
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return self.source["slug"]


Parser = Callable[[Any, ParseContext], Iterable[Any]]
PARSERS: dict[str, Parser] = {}


def register(name: str):
    def deco(fn: Parser) -> Parser:
        PARSERS[name] = fn
        return fn
    return deco


def jget(obj: Any, path: str | None, default: Any = None) -> Any:
    """Dotted path accessor supporting list indexes: '0.pages', 'meta.count', 'message.items'."""
    if not path:
        return default
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return default
        elif isinstance(cur, dict):
            if part not in cur:
                return default
            cur = cur[part]
        else:
            return default
    return cur


def obs(**kw) -> dict[str, Any]:
    kw["item_type"] = "observation"
    return kw


def doc(**kw) -> dict[str, Any]:
    kw["item_type"] = "document"
    return kw


# register built-in parsers
from . import apis, documents, generic, pinksheet  # noqa: E402,F401
