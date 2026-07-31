"""Money helpers.

All money is handled as Decimal and rounded to two places at the boundary.
Floats are deliberately avoided: 0.1 + 0.2 problems in an order total are the
kind of bug that shows up as a one-piastre mismatch against the gateway.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")


def to_money(value):
    """Coerce anything numeric into a 2dp Decimal."""
    if value in (None, ""):
        return ZERO
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def shipping_fee():
    return to_money(settings.STORE_SHIPPING_FEE)


def free_shipping_threshold():
    """Subtotal at or above which shipping is free. Zero disables the offer."""
    return to_money(settings.STORE_FREE_SHIPPING_THRESHOLD)


def shipping_for(subtotal):
    """Shipping cost for a given subtotal."""
    subtotal = to_money(subtotal)
    if subtotal <= ZERO:
        return ZERO
    threshold = free_shipping_threshold()
    if threshold > ZERO and subtotal >= threshold:
        return ZERO
    return shipping_fee()


def amount_until_free_shipping(subtotal):
    """How much more the customer needs to spend to unlock free shipping."""
    threshold = free_shipping_threshold()
    if threshold <= ZERO:
        return ZERO
    remaining = threshold - to_money(subtotal)
    return remaining if remaining > ZERO else ZERO


def currency_code():
    return settings.STORE_CURRENCY


def currency_label():
    return settings.STORE_CURRENCY_LABEL
