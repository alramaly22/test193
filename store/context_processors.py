"""Template context shared across every page."""

from django.conf import settings
from django.utils.functional import SimpleLazyObject

from .cart import get_cart
from .pricing import currency_label


def cart(request):
    """Expose the cart to all templates for the header badge.

    Wrapped in SimpleLazyObject so the database is only touched by pages that
    actually render the cart, rather than on every request including admin.
    """

    def _get_cart():
        if not hasattr(request, "session"):
            return None
        return get_cart(request)

    lazy_cart = SimpleLazyObject(_get_cart)

    return {
        "cart": lazy_cart,
        "cart_count": SimpleLazyObject(lambda: lazy_cart.count if lazy_cart else 0),
        "currency_label": currency_label(),
    }


def tracking(request):
    """IDs and handles used by the base template.

    Only the pixel ID is exposed. The Events API access token stays server side
    and is never rendered into a page.
    """
    return {
        "TIKTOK_PIXEL_ID": settings.TIKTOK_PIXEL_ID,
        "WHATSAPP_NUMBER": settings.STORE_WHATSAPP_NUMBER,
    }
