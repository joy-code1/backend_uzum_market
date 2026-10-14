"""Маршруты API (§5 ТЗ). Все эндпоинты под /api/, плюс /products/gen/* и админка."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.core.views import DemoResetView, HealthView
from apps.products.gen_media import SeedMediaView

urlpatterns = [
    path("/", include("apps.products.urls")),
    path("admin/", admin.site.urls),
    path("api/auth/", include("apps.users.urls")),
    path("api/", include("apps.products.urls")),
    path("api/", include("apps.orders.urls")),
    path("api/", include("apps.uploads.urls")),
    path("api/health", HealthView.as_view(), name="health"),
    path("api/demo/reset/", DemoResetView.as_view(), name="demo-reset"),
    path("products/gen/<str:name>", SeedMediaView.as_view(), name="seed-media"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/schema/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]

if settings.DEBUG or settings.SERVE_MEDIA:
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
