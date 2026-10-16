"""
Тесты API заказов:
  GET  /api/orders/              — список заказов покупателя
  POST /api/orders/              — создать заказ
  GET  /api/orders/{id}/         — детали заказа
  PATCH /api/orders/{id}/status/ — изменение статуса (продавец)

Тесты кабинета продавца:
  GET  /api/shop/                — профиль магазина
  GET  /api/shop/orders/         — заказы магазина
"""

import pytest

from apps.products.models import Product

ORDERS_URL = "/api/orders/"
SHOP_URL = "/api/shop/"
SHOP_ORDERS_URL = "/api/shop/orders/"


def get_csrf(client):
    return client.get("/api/auth/csrf/").json()["csrf"]


# ─────────────────────────────── Заказы — анонимный доступ
@pytest.mark.django_db
class TestOrdersAnonymous:
    def test_orders_list_anonymous_401(self, api_client):
        resp = api_client.get(ORDERS_URL)
        assert resp.status_code == 401

    def test_create_order_anonymous_401(self, api_client):
        csrf = get_csrf(api_client)
        resp = api_client.post(
            ORDERS_URL,
            {"items": [], "delivery_method": "courier", "payment_method": "card"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 401


# ─────────────────────────────── Заказы — авторизованный
@pytest.mark.django_db
class TestOrdersAuth:
    def test_orders_list_returns_200(self, auth_client):
        resp = auth_client.get(ORDERS_URL)
        assert resp.status_code == 200

    def test_orders_list_envelope(self, auth_client):
        data = auth_client.get(ORDERS_URL).json()
        assert "results" in data
        assert "count" in data

    def test_create_order_success(self, auth_client, product):
        """Создание заказа с одним товаром."""
        csrf = get_csrf(auth_client)
        payload = {
            "items": [{"product_id": product.id, "qty": 2}],
            "delivery_method": "courier",
            "payment_method": "card",
            "address": "Ташкент, ул. Тестовая, 1",
        }
        resp = auth_client.post(
            ORDERS_URL,
            payload,
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        # Проверяем статус через детальную карточку
        detail = auth_client.get(f"{ORDERS_URL}{data['id']}/").json()
        assert detail["status"] == "new"

    def test_create_order_reduces_stock(self, auth_client, product):
        """После заказа остаток товара должен уменьшиться."""
        initial_stock = product.stock  # 10
        qty = 3
        csrf = get_csrf(auth_client)
        resp = auth_client.post(
            ORDERS_URL,
            {
                "items": [{"product_id": product.id, "qty": qty}],
                "delivery_method": "courier",
                "payment_method": "card",
                "address": "Ташкент, ул. Тестовая, 2",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 201
        product.refresh_from_db()
        assert product.stock == initial_stock - qty

    def test_create_order_empty_items_400(self, auth_client):
        csrf = get_csrf(auth_client)
        resp = auth_client.post(
            ORDERS_URL,
            {"items": [], "delivery_method": "courier", "payment_method": "card"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 400

    def test_create_order_excess_qty_400(self, auth_client, product):
        """Нельзя заказать больше, чем есть на складе."""
        csrf = get_csrf(auth_client)
        resp = auth_client.post(
            ORDERS_URL,
            {
                "items": [{"product_id": product.id, "qty": 9999}],
                "delivery_method": "courier",
                "payment_method": "card",
                "address": "Ташкент, тест",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 400

    def test_create_order_inactive_product_400(self, auth_client, category, seller):
        """Нельзя заказать неактивный (черновик) товар."""
        draft = Product.objects.create(
            title="Черновик нельзя купить",
            slug="draft-cannot-buy",
            description="Описание черновика товара длиннее двадцати символов.",
            price=100_000,
            stock=5,
            category=category,
            seller=seller,
            status=Product.Status.DRAFT,
        )
        csrf = get_csrf(auth_client)
        resp = auth_client.post(
            ORDERS_URL,
            {
                "items": [{"product_id": draft.id, "qty": 1}],
                "delivery_method": "courier",
                "payment_method": "card",
                "address": "Ташкент",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 400

    def test_order_detail_200(self, auth_client, product):
        csrf = get_csrf(auth_client)
        create_resp = auth_client.post(
            ORDERS_URL,
            {
                "items": [{"product_id": product.id, "qty": 1}],
                "delivery_method": "courier",
                "payment_method": "card",
                "address": "Ташкент, детальный тест",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert create_resp.status_code == 201
        order_id = create_resp.json()["id"]

        resp = auth_client.get(f"{ORDERS_URL}{order_id}/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == order_id
        assert "items" in data
        assert "total" in data
        assert "subtotal" in data

    def test_order_totals_calculated(self, auth_client, product):
        """subtotal должен быть price * qty."""
        qty = 2
        csrf = get_csrf(auth_client)
        resp = auth_client.post(
            ORDERS_URL,
            {
                "items": [{"product_id": product.id, "qty": qty}],
                "delivery_method": "courier",
                "payment_method": "card",
                "address": "Ташкент, итого тест",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 201
        data = resp.json()
        order_id = data["id"]
        # Проверяем детали заказа
        detail = auth_client.get(f"{ORDERS_URL}{order_id}/").json()
        assert detail["subtotal"] == product.price * qty


# ─────────────────────────────── Кабинет продавца
@pytest.mark.django_db
class TestShop:
    def test_shop_anonymous_401(self, api_client):
        resp = api_client.get(SHOP_URL)
        assert resp.status_code == 401

    def test_shop_buyer_has_no_shop_200(self, auth_client):
        """У покупателя нет магазина — возвращает 200 с null (по ТЗ)."""
        resp = auth_client.get(SHOP_URL)
        assert resp.status_code == 200
        assert resp.json() is None

    def test_shop_seller_returns_200(self, seller_client):
        resp = seller_client.get(SHOP_URL)
        assert resp.status_code == 200

    def test_shop_data_fields(self, seller_client):
        data = seller_client.get(SHOP_URL).json()
        required = {"id", "name", "slug", "rating", "product_count"}
        missing = required - data.keys()
        assert not missing, f"Отсутствуют поля кабинета: {missing}"

    def test_shop_orders_seller_returns_200(self, seller_client):
        resp = seller_client.get(SHOP_ORDERS_URL)
        assert resp.status_code == 200

    def test_shop_orders_has_results(self, seller_client):
        data = seller_client.get(SHOP_ORDERS_URL).json()
        assert "results" in data
        assert "count" in data
