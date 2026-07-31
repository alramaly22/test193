"""Data model for the HANY APPAREL store."""

import secrets
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

ZERO = Decimal("0.00")


def resolve_image(value):
    """Turn a stored image reference into a usable URL.

    Accepts an absolute URL (``https://cdn.example.com/tee.png``), a root
    relative path (``/media/tee.png``) or a path inside the static directory
    (``images/store/tee.png``). This lets imagery live in the repo during
    development and move to a CDN later without a migration, which matters
    because Vercel's runtime filesystem is read-only.
    """
    if not value:
        return ""
    value = value.strip()
    if value.startswith(("http://", "https://", "//", "/")):
        return value
    try:
        return static(value)
    except ValueError:
        # Raised by ManifestStaticFilesStorage when the file is not collected.
        return ""


class SizeChoices(models.TextChoices):
    XS = "XS", "XS"
    S = "S", "S"
    M = "M", "M"
    L = "L", "L"
    XL = "XL", "XL"
    XXL = "XXL", "2XL"


class ProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def with_variants(self):
        """Prefetch variants to avoid an N+1 query on listing pages."""
        return self.prefetch_related("variants")


class Product(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, max_length=220)
    description = models.TextField(blank=True)

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(ZERO)],
    )
    # Optional "was" price, shown struck through to signal a discount.
    compare_at_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(ZERO)],
        help_text="Original price, shown struck through. Leave empty if not on sale.",
    )

    front_image = models.CharField(
        max_length=500,
        help_text="Static path (images/store/tee-front.png) or full https:// URL.",
    )
    back_image = models.CharField(
        max_length=500,
        blank=True,
        help_text="Optional second image, revealed on hover.",
    )

    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(
        default=0,
        help_text="Lower numbers appear first.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ("display_order", "-created_at")
        indexes = [
            models.Index(fields=["is_active", "display_order"]),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("product_detail", kwargs={"slug": self.slug})

    @property
    def front_image_url(self):
        return resolve_image(self.front_image)

    @property
    def back_image_url(self):
        return resolve_image(self.back_image)

    @property
    def is_on_sale(self):
        return bool(self.compare_at_price and self.compare_at_price > self.price)

    @property
    def discount_percent(self):
        if not self.is_on_sale:
            return 0
        saved = self.compare_at_price - self.price
        return int((saved / self.compare_at_price) * 100)

    def available_variants(self):
        """In-stock variants, ordered smallest size first."""
        order = {size: index for index, size in enumerate(SizeChoices.values)}
        variants = [v for v in self.variants.all() if v.is_available]
        return sorted(variants, key=lambda v: order.get(v.size, 99))

    @property
    def in_stock(self):
        return any(v.is_available for v in self.variants.all())


class ProductVariant(models.Model):
    """A single sellable size of a product, with its own stock count."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    size = models.CharField(max_length=5, choices=SizeChoices.choices)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        # One row per product/size, enforced by the database rather than by
        # convention, so a duplicate cannot be created through the admin.
        constraints = [
            models.UniqueConstraint(
                fields=["product", "size"],
                name="unique_product_size",
            )
        ]
        ordering = ("product", "size")

    def __str__(self):
        return f"{self.product.name} - {self.size}"

    @property
    def is_available(self):
        return self.is_active and self.stock > 0


def generate_order_number():
    """Human-friendly, non-sequential order reference.

    Non-sequential on purpose: a guessable /order/12/ would let anyone page
    through other customers' orders.
    """
    stamp = timezone.now().strftime("%y%m%d")
    return f"HA-{stamp}-{secrets.token_hex(3).upper()}"


class Order(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash on delivery"
        ONLINE = "online", "Online payment"

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    class OrderStatus(models.TextChoices):
        NEW = "new", "New"
        CONFIRMED = "confirmed", "Confirmed"
        PROCESSING = "processing", "Processing"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    order_number = models.CharField(
        max_length=32,
        unique=True,
        default=generate_order_number,
        editable=False,
    )
    # Unguessable token used in the "thank you" URL so a customer can see their
    # own order without an account, and nobody else's.
    access_token = models.CharField(
        max_length=64,
        default=secrets.token_urlsafe,
        editable=False,
        db_index=True,
    )

    full_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30)
    email = models.EmailField(blank=True)
    governorate = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    address = models.TextField()
    notes = models.TextField(blank=True)

    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    order_status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.NEW,
    )

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    currency = models.CharField(max_length=8, default="EGP")

    # --- Fawaterk bookkeeping -------------------------------------------
    fawaterk_invoice_id = models.CharField(max_length=64, blank=True, db_index=True)
    fawaterk_invoice_key = models.CharField(max_length=128, blank=True)
    fawaterk_payment_method = models.CharField(max_length=64, blank=True)
    fawaterk_reference = models.CharField(max_length=128, blank=True)
    payment_error = models.TextField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    # --- Tracking -------------------------------------------------------
    # Shared between the browser pixel and the Events API call so TikTok can
    # deduplicate the two reports of the same purchase.
    tiktok_event_id = models.CharField(max_length=64, blank=True, editable=False)
    tiktok_purchase_sent = models.BooleanField(default=False, editable=False)

    session_key = models.CharField(max_length=64, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["payment_status", "-created_at"]),
            models.Index(fields=["order_status", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.order_number} - {self.full_name}"

    def get_absolute_url(self):
        return reverse(
            "order_detail",
            kwargs={"order_number": self.order_number, "token": self.access_token},
        )

    @property
    def is_paid(self):
        return self.payment_status == self.PaymentStatus.PAID

    @property
    def requires_payment(self):
        return self.payment_method == self.PaymentMethod.ONLINE and not self.is_paid

    @property
    def item_count(self):
        return sum(item.quantity for item in self.items.all())

    def mark_paid(self, *, invoice_id="", invoice_key="", method="", reference=""):
        """Flag the order as paid. Safe to call more than once.

        Returns True only the first time, so callers can trigger side effects
        (Events API purchase, confirmation email) exactly once even though
        Fawaterk may deliver the same webhook several times.
        """
        if self.payment_status == self.PaymentStatus.PAID:
            return False

        self.payment_status = self.PaymentStatus.PAID
        self.paid_at = timezone.now()
        self.payment_error = ""
        if self.order_status == self.OrderStatus.NEW:
            self.order_status = self.OrderStatus.CONFIRMED
        if invoice_id:
            self.fawaterk_invoice_id = str(invoice_id)
        if invoice_key:
            self.fawaterk_invoice_key = invoice_key
        if method:
            self.fawaterk_payment_method = method
        if reference:
            self.fawaterk_reference = reference
        self.save(
            update_fields=[
                "payment_status",
                "paid_at",
                "payment_error",
                "order_status",
                "fawaterk_invoice_id",
                "fawaterk_invoice_key",
                "fawaterk_payment_method",
                "fawaterk_reference",
                "updated_at",
            ]
        )
        return True

    def mark_failed(self, reason="", *, status=None):
        """Record a declined or cancelled payment without destroying the order."""
        if self.payment_status == self.PaymentStatus.PAID:
            # A late failure notification must never unpay a paid order.
            return False
        self.payment_status = status or self.PaymentStatus.FAILED
        self.payment_error = (reason or "")[:1000]
        self.save(update_fields=["payment_status", "payment_error", "updated_at"])
        return True

    def release_stock(self):
        """Return reserved stock to inventory (used when a payment fails)."""
        for item in self.items.all():
            if item.variant_id:
                ProductVariant.objects.filter(pk=item.variant_id).update(
                    stock=models.F("stock") + item.quantity
                )


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    # SET_NULL rather than CASCADE: deleting a discontinued product must not
    # erase the historical orders that contain it.
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    # Snapshots, so the order stays accurate after a rename or a price change.
    product_name = models.CharField(max_length=200)
    product_image = models.CharField(max_length=500, blank=True)
    size = models.CharField(max_length=5, choices=SizeChoices.choices)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ("id",)

    def __str__(self):
        return f"{self.product_name} ({self.size}) x {self.quantity}"

    @property
    def line_total(self):
        return (self.unit_price or ZERO) * self.quantity

    @property
    def image_url(self):
        return resolve_image(self.product_image)


class WebhookEvent(models.Model):
    """Audit log of every gateway callback received.

    Kept for two reasons: reconciling disputes against what Fawaterk actually
    sent, and diagnosing signature failures without having to reproduce them.
    """

    SOURCE_CHOICES = [
        ("fawaterk_paid", "Fawaterk - paid"),
        ("fawaterk_failed", "Fawaterk - failed"),
        ("fawaterk_expired", "Fawaterk - expired"),
        ("fawaterk_refund", "Fawaterk - refund"),
        ("unknown", "Unknown"),
    ]

    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, default="unknown")
    invoice_id = models.CharField(max_length=64, blank=True, db_index=True)
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_events",
    )
    payload = models.JSONField(default=dict)
    signature_valid = models.BooleanField(default=False)
    processed = models.BooleanField(default=False)
    message = models.CharField(max_length=255, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-received_at",)
        verbose_name = "Webhook event"

    def __str__(self):
        return f"{self.source} #{self.invoice_id or '-'}"
