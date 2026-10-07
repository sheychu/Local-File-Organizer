"""Jinja environment and custom filters shared by document and e-mail rendering."""
from __future__ import annotations

import datetime as dt

import jinja2


def _fmt_date(value, fmt: str = "%d/%m/%Y") -> str:
    """Jinja filter: {{ effective_date | date }} or {{ effective_date | date('%d %B %Y') }}."""
    if not value:
        return ""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime(fmt)
    s = str(value).strip()
    for parse in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, parse).strftime(fmt)
        except ValueError:
            continue
    return s


def _fmt_money(value, currency: str = "EUR") -> str:
    """Jinja filter: {{ amount | money }} → 12.345,00 EUR (Italian style)."""
    if value in ("", None):
        return ""
    try:
        n = float(str(value).replace(",", "."))
    except ValueError:
        return str(value)
    s = f"{n:,.2f}"                       # 12,345.00
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")  # 12.345,00
    return f"{s} {currency}".strip()


def _lines(value):
    """Jinja filter: keep line breaks from a textarea inside the .docx: {{ notes | lines }}."""
    from docxtpl import Listing
    return Listing("" if value is None else str(value))


def make_jinja_env(autoescape: bool = False) -> jinja2.Environment:
    """autoescape=True when rendering into .docx XML (so '&', '<' survive); False for e-mail text."""
    env = jinja2.Environment(autoescape=autoescape, undefined=jinja2.ChainableUndefined)
    env.filters["date"] = _fmt_date
    env.filters["money"] = _fmt_money
    env.filters["lines"] = _lines
    env.filters["upper"] = lambda v: str(v or "").upper()
    return env
