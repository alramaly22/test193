"""Session-backed shopping cart.

Design notes
------------
The session stores only identifiers and quantities. Prices, names and stock are
always re-read from the database when the cart is rendered, so a customer
cannot edit their cookie to change what something costs, and a price change in
the admin is reflected immediately in every open cart.
"""

import logging

from django.conf import settings

from .models import Product, ProductVariant, SizeChoices
from .pricing import ZERO, shipping_for, to_money

logger = logging.getLogger(__name__)

CART_SESSION_KEY = "cart"


def line_key(product_id, size):
    """Identify a cart line. Same product in two sizes is two lines."""
    return f"{product_id}:{size}"


class CartLine:
    """One resolved cart line, ready for display."""

    __slots__ = ("key", "product", "variant", "size", "quantity", "unit_price")

    def __init__(self, key, product, variant, size, quantity, unit_price):
        self.key = key
        self.product = product
        self.variant = variant
        self.size = size
        self.quantity = quantity
        self.unit_price = unit_price

    @property
    def line_total(self):
        return to_money(self.unit_price * self.quantity)

    @property
    def max_quantity(self):
        stock = self.variant.stock if self.variant else 0
        return min(stock, settings.STORE_MAX_ITEM_QUANTITY)

    @property
    def image_url(self):
        return self.product.front_image_url


class Cart:
    """Wraps the cart stored in ``request.session``."""

    def __init__(self, request):
        self.session = request.session
        raw = self.session.get(CART_SESSION_KEY)
        if not isinstance(raw, dict):
            raw = {}
        self._raw = raw
        self._lines = None  # resolved lazily, then cached per request
        # Set when resolving had to trim or drop something, so checkout can stop
        # and tell the customer instead of quietly changing their order.
        self.adjusted = False
        self.adjustment_message = ""

    # -- persistence ----------------------------------------------------

    def save(self):
        self.session[CART_SESSION_KEY] = self._raw
        self.session.modified = True
        self._lines = None  # force a re-resolve on next access

    def clear(self):
        self._raw = {}
        self.session[CART_SESSION_KEY] = {}
        self.session.modified = True
        self._lines = []

    # -- mutation -------------------------------------------------------

    def add(self, product, size, quantity=1, *, replace=False):
        """Add a product/size to the cart.

        Returns (ok, message). The quantity is clamped to available stock
        rather than rejected outright, so a customer asking for 5 of the 3 left
        gets 3 and a clear message instead of a dead end.
        """
        if size not in SizeChoices.values:
            return False, "Choose a valid size."

        variant = product.variants.filter(size=size, is_active=True).first()
        if not variant or variant.stock <= 0:
            return False, f"Size {size} is sold out."

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 1
        if quantity < 1:
            quantity = 1

        key = line_key(product.pk, size)
        current = 0 if replace else self._raw.get(key, {}).get("quantity", 0)
        requested = quantity if replace else current + quantity

        ceiling = min(variant.stock, settings.STORE_MAX_ITEM_QUANTITY)
        final = min(requested, ceiling)

        self._raw[key] = {
            "product_id": product.pk,
            "size": size,
            "quantity": final,
        }
        self.save()

        if final < requested:
            if final == variant.stock:
                return True, f"Only {final} left in size {size}."
            return True, f"Limit of {ceiling} per item."
        return True, f"{product.name} ({size}) added to your bag."

    def set_quantity(self, key, quantity):
        """Set an exact quantity. Zero or less removes the line."""
        entry = self._raw.get(key)
        if not entry:
            return False, "That item is no longer in your bag."

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return False, "Enter a valid quantity."

        if quantity <= 0:
            return self.remove(key)

        variant = ProductVariant.objects.filter(
            product_id=entry["product_id"],
            size=entry["size"],
            is_active=True,
        ).first()
        if not variant or variant.stock <= 0:
            self.remove(key)
            return False, "That size just sold out and was removed from your bag."

        ceiling = min(variant.stock, settings.STORE_MAX_ITEM_QUANTITY)
        final = min(quantity, ceiling)
        entry["quantity"] = final
        self.save()

        if final < quantity:
            return True, f"Only {final} available."
        return True, "Bag updated."

    def remove(self, key):
        if key in self._raw:
            del self._raw[key]
            self.save()
            return True, "Item removed."
        return False, "That item is no longer in your bag."

    # -- reading --------------------------------------------------------

    @property
    def lines(self):
        if self._lines is None:
            self._lines = self._resolve()
        return self._lines

    def _resolve(self):
        """Join the session data against the database in a single query pass.

        Lines whose product or size has since been deactivated are dropped, and
        quantities above remaining stock are trimmed, so the cart can never
        present something that cannot actually be sold.
        """
        if not self._raw:
            return []

        product_ids = {
            entry.get("product_id")
            for entry in self._raw.values()
            if isinstance(entry, dict) and entry.get("product_id")
        }
        if not product_ids:
            return []

        products = {
            product.pk: product
            for product in Product.objects.active()
            .with_variants()
            .filter(pk__in=product_ids)
        }

        lines = []
        changed = False
        self.adjusted = False
        self.adjustment_message = ""

        for key, entry in list(self._raw.items()):
            if not isinstance(entry, dict):
                del self._raw[key]
                changed = True
                continue

            product = products.get(entry.get("product_id"))
            size = entry.get("size")
            if not product or size not in SizeChoices.values:
                del self._raw[key]
                changed = True
                self.adjusted = True
                self.adjustment_message = (
                    "An item in your bag is no longer available and was removed."
                )
                continue

            variant = next(
                (v for v in product.variants.all() if v.size == size and v.is_active),
                None,
            )
            if not variant or variant.stock <= 0:
                del self._raw[key]
                changed = True
                self.adjusted = True
                self.adjustment_message = (
                    f"{product.name} in size {size} has sold out and was removed "
                    "from your bag."
                )
                continue

            quantity = entry.get("quantity", 1)
            try:
                quantity = int(quantity)
            except (TypeError, ValueError):
                quantity = 1

            ceiling = min(variant.stock, settings.STORE_MAX_ITEM_QUANTITY)
            clamped = max(1, min(quantity, ceiling))
            if clamped != quantity:
                entry["quantity"] = clamped
                changed = True
                self.adjusted = True
                self.adjustment_message = (
                    f"Only {clamped} left of {product.name} in size {size}. "
                    "Your bag has been updated."
                )

            lines.append(
                CartLine(
                    key=key,
                    product=product,
                    variant=variant,
                    size=size,
                    quantity=clamped,
                    unit_price=to_money(product.price),
                )
            )

        if changed:
            self.session[CART_SESSION_KEY] = self._raw
            self.session.modified = True

        return lines

    def __iter__(self):
        return iter(self.lines)

    def __len__(self):
        return len(self.lines)

    def __bool__(self):
        return bool(self.lines)

    @property
    def count(self):
        """Total number of garments, used for the header badge."""
        return sum(line.quantity for line in self.lines)

    @property
    def subtotal(self):
        return to_money(sum((line.line_total for line in self.lines), ZERO))

    @property
    def shipping(self):
        return shipping_for(self.subtotal)

    @property
    def total(self):
        return to_money(self.subtotal + self.shipping)

    def to_json(self):
        """Serialise the cart for the JSON responses used by the AJAX flows."""
        return {
            "count": self.count,
            "subtotal": str(self.subtotal),
            "shipping": str(self.shipping),
            "total": str(self.total),
            "lines": [
                {
                    "key": line.key,
                    "slug": line.product.slug,
                    "name": line.product.name,
                    "size": line.size,
                    "quantity": line.quantity,
                    "unit_price": str(line.unit_price),
                    "line_total": str(line.line_total),
                    "image": line.image_url,
                    "max_quantity": line.max_quantity,
                }
                for line in self.lines
            ],
        }

    def tiktok_contents(self):
        """Cart contents in the shape TikTok's ``contents`` property expects."""
        return [
            {
                "content_id": line.product.slug,
                "content_type": "product",
                "content_name": line.product.name,
                "quantity": line.quantity,
                "price": float(line.unit_price),
            }
            for line in self.lines
        ]


def get_cart(request):
    """Return the cart for this request, building it at most once.

    The view and the header badge both need the cart. Without this the page
    would resolve it twice and run the product and variant queries twice for
    every request.
    """
    cached = getattr(request, "_store_cart", None)
    if cached is None:
        cached = Cart(request)
        request._store_cart = cached
    return cached
