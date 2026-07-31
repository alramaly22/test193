"""Store views: catalogue, cart, checkout, payment return and gateway webhook."""

import json
import logging

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import F
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .cart import get_cart
from .forms import CheckoutForm
from .models import Order, OrderItem, Product, ProductVariant, WebhookEvent
from .pricing import (
    amount_until_free_shipping,
    free_shipping_threshold,
    shipping_for,
    to_money,
)
from .services import fawaterk, notifications, tiktok

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wants_json(request):
    """True for fetch() calls, false for a plain form post."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    return "application/json" in request.headers.get("Accept", "")


def _json_or_redirect(request, *, ok, message, fallback, status=200):
    """Answer AJAX with JSON, and a normal form post with a redirect."""
    if _wants_json(request):
        cart = get_cart(request)
        return JsonResponse(
            {"ok": ok, "message": message, "cart": cart.to_json()},
            status=status if ok else 400,
        )
    if message:
        (messages.success if ok else messages.error)(request, message)
    return redirect(fallback)


def _absolute(request, path):
    return request.build_absolute_uri(path)


def _on_payment_confirmed(order, request=None):
    """Side effects that run exactly once, when a payment is first verified.

    Called from both confirmation paths (the webhook and the success-page
    lookup), so whichever arrives first wins and the other becomes a no-op.
    The caller is responsible for only invoking this when ``mark_paid()``
    returned True.

    Neither call below can raise: analytics and email are best-effort and must
    never turn a successful payment into an error response.
    """
    logger.info("Payment confirmed for order %s", order.order_number)
    tiktok.send_purchase(order, request=request)
    notifications.send_order_notification(order, reason="paid")


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

@require_GET
def store(request):
    products = list(Product.objects.active().with_variants())

    return render(
        request,
        "store/store.html",
        {
            "products": products,
            "free_shipping_threshold": free_shipping_threshold(),
            "page_title": "HANY APPAREL",
        },
    )


@require_GET
def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.active().with_variants(),
        slug=slug,
    )

    variants = product.available_variants()

    # ViewContent, reported from the server as well as the pixel. The shared
    # event id lets TikTok collapse the two into one.
    event_id = tiktok.new_event_id()
    tiktok.send_event(
        tiktok.VIEW_CONTENT,
        event_id=event_id,
        request=request,
        properties={
            "currency": settings.STORE_CURRENCY,
            "value": float(product.price),
            "content_type": "product",
            "contents": [
                {
                    "content_id": product.slug,
                    "content_type": "product",
                    "content_name": product.name,
                    "quantity": 1,
                    "price": float(product.price),
                }
            ],
        },
    )

    related = (
        Product.objects.active()
        .with_variants()
        .exclude(pk=product.pk)[:3]
    )

    return render(
        request,
        "store/product_detail.html",
        {
            "product": product,
            "variants": variants,
            "related_products": related,
            "view_content_event_id": event_id,
            "max_quantity": settings.STORE_MAX_ITEM_QUANTITY,
            "page_title": product.name,
        },
    )


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------

@never_cache
@require_GET
def cart_view(request):
    cart = get_cart(request)
    subtotal = cart.subtotal

    return render(
        request,
        "store/cart.html",
        {
            "cart": cart,
            "subtotal": subtotal,
            "shipping": cart.shipping,
            "total": cart.total,
            "until_free_shipping": amount_until_free_shipping(subtotal),
            "free_shipping_threshold": free_shipping_threshold(),
            "page_title": "Your bag",
        },
    )


@require_POST
def cart_add(request):
    slug = (request.POST.get("slug") or "").strip()
    size = (request.POST.get("size") or "").strip().upper()
    quantity = request.POST.get("quantity", 1)

    product = Product.objects.active().with_variants().filter(slug=slug).first()
    if not product:
        return _json_or_redirect(
            request,
            ok=False,
            message="That product is no longer available.",
            fallback="store",
        )

    if not size:
        return _json_or_redirect(
            request,
            ok=False,
            message="Choose a size first.",
            fallback=product.get_absolute_url(),
        )

    cart = get_cart(request)
    ok, message = cart.add(product, size, quantity)

    event_id = ""
    if ok:
        # One id for both reports of this add-to-cart, so TikTok deduplicates.
        event_id = tiktok.new_event_id()
        tiktok.send_event(
            tiktok.ADD_TO_CART,
            event_id=event_id,
            request=request,
            properties={
                "currency": settings.STORE_CURRENCY,
                "value": float(to_money(product.price)),
                "content_type": "product",
                "contents": [
                    {
                        "content_id": product.slug,
                        "content_type": "product",
                        "content_name": product.name,
                        "quantity": int(quantity or 1),
                        "price": float(to_money(product.price)),
                    }
                ],
            },
        )

    if _wants_json(request):
        return JsonResponse(
            {
                "ok": ok,
                "message": message,
                "event_id": event_id,
                "cart": cart.to_json(),
            },
            status=200 if ok else 400,
        )

    return _json_or_redirect(
        request,
        ok=ok,
        message=message,
        fallback=product.get_absolute_url(),
    )


@require_POST
def cart_update(request):
    key = (request.POST.get("key") or "").strip()
    quantity = request.POST.get("quantity", 0)

    cart = get_cart(request)
    ok, message = cart.set_quantity(key, quantity)
    return _json_or_redirect(request, ok=ok, message=message, fallback="cart")


@require_POST
def cart_remove(request):
    key = (request.POST.get("key") or "").strip()
    cart = get_cart(request)
    ok, message = cart.remove(key)
    return _json_or_redirect(request, ok=ok, message=message, fallback="cart")


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

class _OutOfStock(Exception):
    """Internal signal that a cart line cannot be fulfilled."""


@never_cache
def checkout(request):
    cart = get_cart(request)

    if not cart:
        messages.info(request, "Your bag is empty.")
        return redirect("store")

    checkout_event_id = ""

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            return _place_order(request, cart, form)
        messages.error(request, "Please check the highlighted fields.")
    else:
        form = CheckoutForm()
        # InitiateCheckout fires once, when the page is first opened, not on a
        # failed re-submit, so the funnel numbers stay honest.
        checkout_event_id = tiktok.new_event_id()
        tiktok.send_event(
            tiktok.INITIATE_CHECKOUT,
            event_id=checkout_event_id,
            request=request,
            properties={
                "currency": settings.STORE_CURRENCY,
                "value": float(cart.total),
                "content_type": "product",
                "contents": cart.tiktok_contents(),
            },
        )

    return render(
        request,
        "store/checkout.html",
        {
            "form": form,
            "cart": cart,
            "subtotal": cart.subtotal,
            "shipping": cart.shipping,
            "total": cart.total,
            "online_payment_available": fawaterk.is_configured(),
            "checkout_event_id": checkout_event_id,
            "tiktok_contents": cart.tiktok_contents(),
            "page_title": "Checkout",
        },
    )


def _place_order(request, cart, form):
    """Create the order, reserve stock and hand off to payment.

    Everything runs inside one transaction and takes a row lock on each variant,
    so two people racing for the last shirt cannot both succeed. If any line has
    since sold out the whole thing rolls back and the customer is told which
    item is the problem.
    """
    lines = cart.lines
    if not lines:
        messages.info(request, "Your bag is empty.")
        return redirect("store")

    # Resolving the cart may have trimmed a quantity or dropped a sold-out line.
    # The customer reviewed different numbers, so send them back to look rather
    # than charging them for an order they did not confirm.
    if cart.adjusted:
        messages.error(
            request,
            cart.adjustment_message
            or "Your bag changed while you were checking out. Please review it.",
        )
        return redirect("cart")

    try:
        with transaction.atomic():
            variant_ids = [line.variant.pk for line in lines]
            locked = {
                variant.pk: variant
                for variant in ProductVariant.objects.select_for_update().filter(
                    pk__in=variant_ids
                )
            }

            for line in lines:
                variant = locked.get(line.variant.pk)
                if not variant or not variant.is_active:
                    raise _OutOfStock(f"{line.product.name} ({line.size}) is no longer available.")
                if variant.stock < line.quantity:
                    raise _OutOfStock(
                        f"Only {variant.stock} left of {line.product.name} in size {line.size}."
                    )

            subtotal = to_money(sum(line.line_total for line in lines))
            shipping = shipping_for(subtotal)
            total = to_money(subtotal + shipping)

            order = form.save(commit=False)
            order.payment_method = form.cleaned_data["payment"]
            order.subtotal = subtotal
            order.shipping_cost = shipping
            order.total_price = total
            order.currency = settings.STORE_CURRENCY
            order.session_key = request.session.session_key or ""
            order.tiktok_event_id = tiktok.new_event_id()
            order.save()

            OrderItem.objects.bulk_create(
                [
                    OrderItem(
                        order=order,
                        product=line.product,
                        variant=line.variant,
                        product_name=line.product.name,
                        product_image=line.product.front_image,
                        size=line.size,
                        quantity=line.quantity,
                        unit_price=line.unit_price,
                    )
                    for line in lines
                ]
            )

            # Reserve stock now. Released again if an online payment fails.
            for line in lines:
                ProductVariant.objects.filter(pk=line.variant.pk).update(
                    stock=F("stock") - line.quantity
                )

    except _OutOfStock as exc:
        messages.error(request, str(exc))
        return redirect("cart")

    logger.info(
        "Order %s created (%s, %s %s)",
        order.order_number,
        order.payment_method,
        order.total_price,
        order.currency,
    )

    # PlaceAnOrder marks intent, and fires for cash and card alike. The paid
    # conversion (CompletePayment) is reported separately, only once money
    # actually arrives.
    tiktok.send_event(
        tiktok.PLACE_AN_ORDER,
        request=request,
        properties=tiktok.order_properties(order),
        user=tiktok.user_payload(
            request,
            email=order.email,
            phone=order.phone,
            external_id=order.order_number,
        ),
    )

    if order.payment_method == Order.PaymentMethod.CASH:
        # A cash order is never "paid" online, so it would otherwise never
        # trigger a notification and the owner would not know about it.
        if settings.NOTIFY_ON_COD_ORDER:
            notifications.send_order_notification(order, reason="cod")
        cart.clear()
        return redirect(order.get_absolute_url())

    return _start_online_payment(request, cart, order)


def _start_online_payment(request, cart, order):
    """Create a Fawaterk invoice and redirect the customer to it."""
    if not fawaterk.is_configured():
        order.mark_failed("Online payment is not configured.")
        order.release_stock()
        messages.error(
            request,
            "Card payment is unavailable right now. Choose cash on delivery, "
            "or contact us on WhatsApp and we will take the order manually.",
        )
        return redirect("checkout")

    return_kwargs = {
        "order_number": order.order_number,
        "token": order.access_token,
    }

    try:
        invoice = fawaterk.create_invoice(
            order,
            success_url=_absolute(request, reverse("payment_success", kwargs=return_kwargs)),
            fail_url=_absolute(request, reverse("payment_failed", kwargs=return_kwargs)),
            pending_url=_absolute(request, reverse("payment_pending", kwargs=return_kwargs)),
            webhook_url=_absolute(request, reverse("fawaterk_webhook")),
        )
    except fawaterk.FawaterkError as exc:
        logger.error("Fawaterk invoice failed for %s: %s", order.order_number, exc)
        order.mark_failed(str(exc))
        order.release_stock()
        messages.error(
            request,
            "We could not open the payment page. Nothing has been charged. "
            "Please try again, or choose cash on delivery.",
        )
        return redirect("checkout")

    order.fawaterk_invoice_id = invoice["invoice_id"]
    order.fawaterk_invoice_key = invoice["invoice_key"]
    order.save(update_fields=["fawaterk_invoice_id", "fawaterk_invoice_key", "updated_at"])

    # The cart is cleared once the invoice exists: the order now holds the
    # items, and leaving the cart populated invites a duplicate order.
    cart.clear()

    return redirect(invoice["url"])


# ---------------------------------------------------------------------------
# Order and payment return pages
# ---------------------------------------------------------------------------

def _get_order(order_number, token):
    """Fetch an order by number *and* token, so the URL cannot be guessed."""
    order = (
        Order.objects.filter(order_number=order_number, access_token=token)
        .prefetch_related("items")
        .first()
    )
    if not order:
        raise Http404("Order not found.")
    return order


@never_cache
@require_GET
def order_detail(request, order_number, token):
    order = _get_order(order_number, token)

    return render(
        request,
        "store/order_detail.html",
        {
            "order": order,
            "items": order.items.all(),
            "page_title": f"Order {order.order_number}",
        },
    )


@never_cache
@require_GET
def payment_success(request, order_number, token):
    """Landing page after a successful payment.

    The redirect itself is not proof of payment: a customer can bookmark or
    hand-edit this URL. So we confirm against the gateway before showing a paid
    receipt, and fall back to "pending" if we cannot confirm. The webhook is
    what usually marks the order paid; this is the belt to its braces.
    """
    order = _get_order(order_number, token)

    if not order.is_paid and order.fawaterk_invoice_id:
        paid, data = fawaterk.is_invoice_paid(order.fawaterk_invoice_id)
        if paid:
            newly_paid = order.mark_paid(
                invoice_id=order.fawaterk_invoice_id,
                method=(data or {}).get("payment_method", ""),
            )
            if newly_paid:
                logger.info(
                    "Order %s confirmed paid via success redirect", order.order_number
                )
                _on_payment_confirmed(order, request=request)

    return render(
        request,
        "store/payment_result.html",
        {
            "order": order,
            "items": order.items.all(),
            "outcome": "success" if order.is_paid else "pending",
            "page_title": "Payment received" if order.is_paid else "Payment pending",
        },
    )


@never_cache
@require_GET
def payment_failed(request, order_number, token):
    order = _get_order(order_number, token)

    # Only touch the order if the gateway has not already confirmed payment.
    if not order.is_paid and order.payment_status == Order.PaymentStatus.PENDING:
        order.mark_failed("Payment was cancelled or declined.")
        order.release_stock()

    return render(
        request,
        "store/payment_result.html",
        {
            "order": order,
            "items": order.items.all(),
            "outcome": "failed",
            "page_title": "Payment not completed",
        },
    )


@never_cache
@require_GET
def payment_pending(request, order_number, token):
    """Used for Fawry / Aman style codes that are paid later at an outlet."""
    order = _get_order(order_number, token)

    return render(
        request,
        "store/payment_result.html",
        {
            "order": order,
            "items": order.items.all(),
            "outcome": "pending",
            "page_title": "Payment pending",
        },
    )


@require_POST
def retry_payment(request, order_number, token):
    """Open a fresh invoice for an order whose first attempt failed."""
    order = _get_order(order_number, token)

    if order.is_paid:
        return redirect(order.get_absolute_url())
    if order.payment_method != Order.PaymentMethod.ONLINE:
        return redirect(order.get_absolute_url())

    # Re-reserve stock, since a failed attempt released it.
    for item in order.items.all():
        if item.variant_id:
            variant = ProductVariant.objects.filter(pk=item.variant_id).first()
            if not variant or variant.stock < item.quantity:
                messages.error(
                    request,
                    f"{item.product_name} ({item.size}) has sold out. "
                    "Please start a new order.",
                )
                return redirect("store")

    with transaction.atomic():
        for item in order.items.all():
            if item.variant_id:
                ProductVariant.objects.filter(pk=item.variant_id).update(
                    stock=F("stock") - item.quantity
                )
        order.payment_status = Order.PaymentStatus.PENDING
        order.payment_error = ""
        order.save(update_fields=["payment_status", "payment_error", "updated_at"])

    return _start_online_payment(request, get_cart(request), order)


# ---------------------------------------------------------------------------
# Fawaterk webhook
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def fawaterk_webhook(request):
    """Receive payment notifications from Fawaterk.

    Notes for whoever maintains this next:

    * The URL must contain ``_json`` for Fawaterk to send a JSON body. It sends
      form-encoded data otherwise, so both are handled here.
    * ``csrf_exempt`` is required: the gateway has no CSRF token. Authenticity
      comes from the HMAC instead, which is checked before anything is trusted.
    * We always answer 200 for a payload we understood, even if we could not
      match an order. A non-2xx makes Fawaterk retry the same event forever.
    * Every callback is written to WebhookEvent first, so a signature mismatch
      or an unmatched invoice can be investigated after the fact.
    """
    payload = _parse_webhook_body(request)
    if payload is None:
        return JsonResponse({"status": "error", "message": "Invalid payload"}, status=400)

    kind = _classify_webhook(payload)
    invoice_id = str(payload.get("invoice_id") or "")

    if kind == "fawaterk_expired":
        signature_valid = fawaterk.verify_expired_hash(payload)
    elif kind == "fawaterk_refund":
        # Refund callbacks carry no hashKey in the documented payload.
        signature_valid = True
    else:
        signature_valid = fawaterk.verify_invoice_hash(payload)

    event = WebhookEvent.objects.create(
        source=kind,
        invoice_id=invoice_id,
        payload=payload,
        signature_valid=signature_valid,
    )

    if settings.FAWATERK_VERIFY_WEBHOOK and not signature_valid:
        event.message = "Signature mismatch, ignored"
        event.save(update_fields=["message"])
        logger.warning(
            "Rejected Fawaterk webhook with bad signature (invoice=%s)", invoice_id
        )
        return JsonResponse({"status": "error", "message": "Invalid signature"}, status=403)

    order = _find_order(payload)
    if not order:
        event.message = "No matching order"
        event.save(update_fields=["message"])
        logger.warning("Fawaterk webhook had no matching order: %s", payload)
        return JsonResponse({"status": "ignored", "message": "No matching order"})

    event.order = order

    if kind == "fawaterk_paid":
        newly_paid = order.mark_paid(
            invoice_id=payload.get("invoice_id", ""),
            invoice_key=payload.get("invoice_key", ""),
            method=payload.get("payment_method", ""),
            reference=str(payload.get("referenceNumber") or ""),
        )
        event.processed = True
        event.message = "Order marked paid" if newly_paid else "Already paid, ignored"
        if newly_paid:
            logger.info("Order %s marked paid by webhook", order.order_number)
            # request is not forwarded, so the IP and user agent are omitted
            # rather than wrongly attributed to Fawaterk's server.
            _on_payment_confirmed(order, request=None)

    elif kind in {"fawaterk_failed", "fawaterk_expired"}:
        reason = (
            payload.get("errorMessage")
            or payload.get("status")
            or "Payment failed"
        )
        status = (
            Order.PaymentStatus.CANCELLED
            if kind == "fawaterk_expired"
            else Order.PaymentStatus.FAILED
        )
        changed = order.mark_failed(str(reason), status=status)
        if changed:
            order.release_stock()
        event.processed = True
        event.message = f"Order marked {status}" if changed else "Already paid, ignored"

    elif kind == "fawaterk_refund":
        if str(payload.get("status", "")).lower() == "approved":
            order.payment_status = Order.PaymentStatus.REFUNDED
            order.save(update_fields=["payment_status", "updated_at"])
            event.processed = True
            event.message = "Order marked refunded"

    event.save(update_fields=["order", "processed", "message"])
    return JsonResponse({"status": "success"})


def _parse_webhook_body(request):
    """Accept a JSON body or form-encoded fields."""
    content_type = (request.content_type or "").lower()

    if "application/json" in content_type:
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            logger.warning("Fawaterk webhook body was not valid JSON")
            return None
        return data if isinstance(data, dict) else None

    if request.POST:
        data = request.POST.dict()
        # pay_load arrives as a JSON string when form-encoded.
        raw = data.get("pay_load")
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                data["pay_load"] = json.loads(raw)
            except ValueError:
                pass
        return data

    # Some senders post JSON without the header.
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
        return data if isinstance(data, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _classify_webhook(payload):
    """Work out which of Fawaterk's four callback shapes this is."""
    status = str(payload.get("invoice_status") or "").lower()

    if status == "paid":
        return "fawaterk_paid"
    if str(payload.get("status") or "").upper() == "EXPIRED":
        return "fawaterk_expired"
    if payload.get("errorMessage") or str(payload.get("status") or "").lower() in {
        "failed",
        "declined",
    }:
        return "fawaterk_failed"
    if payload.get("approvedAt") or payload.get("reason"):
        return "fawaterk_refund"
    if payload.get("invoice_id"):
        # A paid callback that omitted invoice_status.
        return "fawaterk_paid"
    return "unknown"


def _find_order(payload):
    """Match a callback to an order.

    Preference order: our own order number echoed back in ``pay_load`` (the
    value we set ourselves), then the invoice id, then the invoice key.
    """
    pay_load = payload.get("pay_load")
    if isinstance(pay_load, str):
        try:
            pay_load = json.loads(pay_load)
        except ValueError:
            pay_load = None

    if isinstance(pay_load, dict):
        order_number = pay_load.get("order_number")
        if order_number:
            order = Order.objects.filter(order_number=order_number).first()
            if order:
                return order

    invoice_id = payload.get("invoice_id")
    if invoice_id:
        order = Order.objects.filter(fawaterk_invoice_id=str(invoice_id)).first()
        if order:
            return order

    invoice_key = payload.get("invoice_key")
    if invoice_key:
        return Order.objects.filter(fawaterk_invoice_key=invoice_key).first()

    return None
