from django.urls import path

from . import views

urlpatterns = [
    path("", views.store, name="store"),
    path("product/<slug:slug>/", views.product_detail, name="product_detail"),

    # Cart. The mutations are POST only and CSRF protected.
    path("cart/", views.cart_view, name="cart"),
    path("cart/add/", views.cart_add, name="cart_add"),
    path("cart/update/", views.cart_update, name="cart_update"),
    path("cart/remove/", views.cart_remove, name="cart_remove"),

    path("checkout/", views.checkout, name="checkout"),

    # Order pages are addressed by order number *and* a random token, so one
    # customer cannot read another's order by editing the URL.
    path(
        "order/<str:order_number>/<str:token>/",
        views.order_detail,
        name="order_detail",
    ),
    path(
        "order/<str:order_number>/<str:token>/success/",
        views.payment_success,
        name="payment_success",
    ),
    path(
        "order/<str:order_number>/<str:token>/failed/",
        views.payment_failed,
        name="payment_failed",
    ),
    path(
        "order/<str:order_number>/<str:token>/pending/",
        views.payment_pending,
        name="payment_pending",
    ),
    path(
        "order/<str:order_number>/<str:token>/retry/",
        views.retry_payment,
        name="retry_payment",
    ),

    # The path must contain "_json" for Fawaterk to send a JSON body rather
    # than form-encoded fields. Register this exact URL in the Fawaterk
    # dashboard under Integrations > Webhooks.
    path("payment/webhook_json/", views.fawaterk_webhook, name="fawaterk_webhook"),
]
