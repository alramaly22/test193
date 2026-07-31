from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from accounts import views
from store import views as store_views

urlpatterns = [
    path("", views.index, name="index"),
    path("about/", views.about, name="about"),
    path("pricing/", views.pricing, name="pricing"),
    path("second/", views.second, name="second"),
    path("book/", views.book, name="book"),

    # Arabic calculators
    path("protein/", views.protein, name="protein"),
    path("calories/", views.calories, name="calories"),

    # English calculators
    path("proteinen/", views.proteinen, name="proteinen"),
    path("caloriesen/", views.caloriesen, name="caloriesen"),

    path("store/", include("store.urls")),

    # Kept so the webhook URL already registered in the Fawaterk dashboard
    # keeps working. New deployments should point at store/payment/webhook_json/
    # instead, which is the URL that receives a JSON body.
    path("webhook/paid/", store_views.fawaterk_webhook, name="paid_webhook_legacy"),

    path("admin/", admin.site.urls),
]

# Django only serves media through this helper in development; on Vercel the
# filesystem is read-only and assets are served from static/ or a CDN.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
