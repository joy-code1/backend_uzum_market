"""
Pytest конфигурация и общие фикстуры для тестов API Uzum Market.

Запуск:
    pytest tests/                        # все тесты
    pytest tests/ -v                     # с подробным выводом
    pytest tests/test_products.py -v     # только тесты продуктов
    pytest tests/ -k "test_health"       # тест по имени
"""

import os

import django
import pytest

# Устанавливаем тестовые настройки ДО импорта django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")
os.environ.setdefault(
    "SECRET_KEY",
    "test-only-secret-key-not-used-anywhere-else-0123456789abcdefghij",
)
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("DEBUG", "True")

django.setup()


# ─────────────────────────────────────────── импорты после django.setup()
from django.contrib.auth import get_user_model  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from apps.products.models import Category, Product, Seller  # noqa: E402

User = get_user_model()

PASSWORD = "Password123"


# ────────────────────────────────────────── вспомогательные функции
def get_csrf(client: APIClient) -> str:
    """Получить CSRF-токен через /api/auth/csrf/."""
    resp = client.get("/api/auth/csrf/")
    assert resp.status_code == 200
    return resp.json()["csrf"]


# ────────────────────────────────────────── базовые фикстуры
@pytest.fixture
def api_client() -> APIClient:
    """Анонимный API-клиент."""
    return APIClient()


@pytest.fixture
def auth_client(db) -> APIClient:
    """Авторизованный API-клиент (покупатель)."""
    client = APIClient()
    user = User.objects.create_user(
        email="buyer@test.uz",
        password=PASSWORD,
        first_name="Тест",
    )
    csrf = get_csrf(client)
    resp = client.post(
        "/api/auth/login/",
        {"email": "buyer@test.uz", "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert resp.status_code == 200, resp.content
    return client


@pytest.fixture
def seller_user(db):
    """Пользователь-продавец с привязанным магазином."""
    user = User.objects.create_user(
        email="seller@test.uz",
        password=PASSWORD,
        first_name="Продавец",
    )
    seller = Seller.objects.create(
        name="Test Shop",
        slug="test-shop",
        owner=user,
    )
    return user, seller


@pytest.fixture
def seller_client(db, seller_user) -> APIClient:
    """Авторизованный API-клиент продавца."""
    user, seller = seller_user
    client = APIClient()
    csrf = get_csrf(client)
    resp = client.post(
        "/api/auth/login/",
        {"email": "seller@test.uz", "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert resp.status_code == 200, resp.content
    return client


@pytest.fixture
def category(db) -> Category:
    """Тестовая категория."""
    return Category.objects.create(
        name="Электроника",
        slug="elektronika",
        emoji="📱",
        color="#EDE9FF",
    )


@pytest.fixture
def seller(db) -> Seller:
    """Тестовый магазин (без владельца)."""
    s, _ = Seller.objects.get_or_create(
        slug="demo-shop",
        defaults={"name": "Demo Shop"},
    )
    return s


@pytest.fixture
def product(db, category, seller) -> Product:
    """Активный тестовый товар."""
    return Product.objects.create(
        title="Тестовый смартфон Samsung Galaxy",
        slug="test-samsung-galaxy",
        description="Тестовое описание товара длиной больше двадцати символов.",
        price=3_500_000,
        old_price=4_000_000,
        stock=10,
        delivery_time="1–2 дня",
        brand="Samsung",
        images=["/products/gen/a-1.svg"],
        characteristics={"Экран": "6.7\"", "Память": "128 ГБ"},
        category=category,
        seller=seller,
        status=Product.Status.ACTIVE,
    )

