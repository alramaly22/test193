"""Template helpers for the store."""

import json

from django import template
from django.utils.safestring import mark_safe

from ..pricing import currency_label, to_money

register = template.Library()


@register.filter
def money(value):
    """Format an amount with the store currency: ``1,250 EGP``.

    Whole amounts drop the decimals, because "125 EGP" reads better on a
    product card than "125.00 EGP".
    """
    amount = to_money(value)
    if amount == amount.to_integral_value():
        formatted = f"{int(amount):,}"
    else:
        formatted = f"{amount:,.2f}"
    return f"{formatted} {currency_label()}"


@register.filter
def plain_money(value):
    """Same formatting without the currency suffix."""
    amount = to_money(value)
    if amount == amount.to_integral_value():
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


@register.filter
def json_script_safe(value):
    """Serialise a value for embedding inside a <script> block.

    Escapes the characters that could otherwise close the tag early, so
    attacker-controlled product text cannot break out into executable script.
    """
    dumped = json.dumps(value)
    dumped = (
        dumped.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return mark_safe(dumped)
