"""Служебные эндпоинты: /api/health и /api/demo/reset/."""

from django.conf import settings
from django.core.management import call_command
from django.db import connection, transaction
from django.http import JsonResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .datetime import iso_utc


class RootView(APIView):
    """GET / — корневой эндпоинт для быстрой проверки доступности бэкенда."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(tags=["service"], responses={200: None})
    def get(self, request):
        return Response(
            {
                "status": "ok",
                "service": "uzum-market-clone",
                "message": "Бэкенд Uzum Market успешно развернут и работает!",
                "time": iso_utc(timezone.now()),
                "endpoints": {
                    "health": "/api/health",
                    "test": "/api/test/",
                    "ping": "/api/ping/",
                    "hello": "/api/hello/",
                    "docs": "/api/docs/",
                    "swagger": "/api/schema/swagger-ui/",
                    "products": "/api/products/",
                    "categories": "/api/categories/",
                },
            }
        )


class PingView(APIView):
    """GET /api/ping/ — простой пинг для мониторинга (возвращает pong)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(tags=["service"], responses={200: None})
    def get(self, request):
        return Response(
            {
                "status": "ok",
                "ping": "pong",
                "message": "Pong! Сервер активен и готов к обработке запросов.",
                "time": iso_utc(timezone.now()),
            }
        )


class TestView(APIView):
    """GET/POST /api/test/ — проверочный эндпоинт для быстрой проверки работы API и деплоя."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(tags=["service"], responses={200: None})
    def get(self, request):
        return Response(
            {
                "status": "success",
                "message": "Тестовый роут работает стабильно! Изменения успешно применились на сервере.",
                "backend": getattr(settings, "BACKEND_ID", "default"),
                "environment": "debug" if settings.DEBUG else "production",
                "client_ip": request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR"),
                "time": iso_utc(timezone.now()),
            }
        )

    @extend_schema(tags=["service"], responses={200: None})
    def post(self, request):
        return Response(
            {
                "status": "success",
                "message": "POST-запрос успешно получен тестовым роутом!",
                "received_data": request.data,
                "time": iso_utc(timezone.now()),
            }
        )


class HelloView(APIView):
    """GET /api/hello/ — приветственный тестовый эндпоинт."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(tags=["service"], responses={200: None})
    def get(self, request):
        name = request.query_params.get("name", "Гость")
        return Response(
            {
                "status": "ok",
                "greeting": f"Привет, {name}!",
                "message": "Это тестовый API-эндпоинт Uzum Market Backend.",
                "time": iso_utc(timezone.now()),
            }
        )


class HealthView(APIView):
    """GET /api/health — readiness-проба. Только чтение, БД не инициализирует."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(tags=["service"], responses={200: None})
    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM products_product")
            products = cursor.fetchone()[0]
        return JsonResponse(
            {
                "status": "ok",
                "service": "uzum-market-clone",
                "backend": settings.BACKEND_ID,
                "products": products,
                "time": iso_utc(timezone.now()),
            }
        )


class DemoResetView(APIView):
    """POST /api/demo/reset/ — вернуть БД к сид-состоянию.

    Только для авторизованных (анониму 401). На проде выключается env-флагом
    UZUM_LOCK_DEMO=1 → 403. После сброса текущая сессия завершается: все
    пользователи пересоздаются, и «старая» кука больше никого не представляет.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = []
    serializer_class = serializers.Serializer

    @extend_schema(tags=["service"], responses={200: None, 401: None, 403: None})
    def post(self, request):
        if settings.LOCK_DEMO:
            return Response(
                {"detail": "Сброс демо-данных отключён на этом сервере."},
                status=status.HTTP_403_FORBIDDEN,
            )
        with transaction.atomic():
            call_command("seed", reset=True, stdout=None, stderr=None)
        request.session.flush()
        response = Response({"detail": "Демо-данные восстановлены"})
        response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
        response.delete_cookie(settings.CSRF_COOKIE_NAME, path="/")
        return response
