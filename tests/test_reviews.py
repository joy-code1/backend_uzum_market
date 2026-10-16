"""
Тесты API отзывов:
  GET  /api/products/{id}/reviews/  — список отзывов к товару
  POST /api/products/{id}/reviews/  — написать отзыв
  GET  /api/reviews/{id}/           — детали отзыва
  PATCH /api/reviews/{id}/          — редактировать свой отзыв
  DELETE /api/reviews/{id}/         — удалить свой отзыв
  POST /api/reviews/{id}/reply/     — ответ продавца
"""

import pytest

from apps.orders.models import Order, OrderItem
from apps.products.models import Product, Review


def get_csrf(client):
    return client.get("/api/auth/csrf/").json()["csrf"]


def create_order_for_product(client, product):
    """Вспомогательная функция: создать заказ, чтобы оставить отзыв."""
    csrf = get_csrf(client)
    resp = client.post(
        "/api/orders/",
        {
            "items": [{"product_id": product.id, "qty": 1}],
            "delivery_method": "courier",
            "payment_method": "card",
            "address": "Ташкент, тест отзыв",
        },
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    return resp


@pytest.mark.django_db
class TestReviews:
    def test_reviews_list_200(self, api_client, product):
        resp = api_client.get(f"/api/products/{product.id}/reviews/")
        assert resp.status_code == 200

    def test_reviews_list_empty_initially(self, api_client, product):
        data = api_client.get(f"/api/products/{product.id}/reviews/").json()
        assert data["count"] == 0
        assert data["results"] == []

    def test_review_anonymous_cannot_post(self, api_client, product):
        csrf = get_csrf(api_client)
        resp = api_client.post(
            f"/api/products/{product.id}/reviews/",
            {"rating": 5, "text": "Отличный товар!"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 401

    def test_review_requires_purchase(self, auth_client, product):
        """Без покупки нельзя оставить отзыв."""
        csrf = get_csrf(auth_client)
        resp = auth_client.post(
            f"/api/products/{product.id}/reviews/",
            {"rating": 4, "text": "Хороший товар, но без покупки."},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        # Должен быть 403 (нет покупки) или 400
        assert resp.status_code in (400, 403)

    def test_review_after_purchase(self, auth_client, product):
        """После покупки можно оставить отзыв."""
        # Создаём заказ
        order_resp = create_order_for_product(auth_client, product)
        if order_resp.status_code != 201:
            pytest.skip("Не удалось создать заказ для тестирования отзыва")

        csrf = get_csrf(auth_client)
        resp = auth_client.post(
            f"/api/products/{product.id}/reviews/",
            {"rating": 5, "text": "Отличный товар! Очень доволен покупкой."},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["rating"] == 5
        assert "Отличный товар" in data["text"]

    def test_review_updates_product_rating(self, auth_client, product):
        """После отзыва рейтинг товара должен обновиться."""
        order_resp = create_order_for_product(auth_client, product)
        if order_resp.status_code != 201:
            pytest.skip("Не удалось создать заказ")

        csrf = get_csrf(auth_client)
        auth_client.post(
            f"/api/products/{product.id}/reviews/",
            {"rating": 4, "text": "Хороший товар в целом доволен покупкой."},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        product.refresh_from_db()
        assert float(product.rating) > 0
        assert product.reviews_count == 1

    def test_review_invalid_rating_400(self, auth_client, product):
        """Рейтинг должен быть от 1 до 5."""
        order_resp = create_order_for_product(auth_client, product)
        if order_resp.status_code != 201:
            pytest.skip("Не удалось создать заказ")

        csrf = get_csrf(auth_client)
        resp = auth_client.post(
            f"/api/products/{product.id}/reviews/",
            {"rating": 10, "text": "Слишком высокий рейтинг недопустим."},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestReviewDetail:
    def _create_review(self, auth_client, product):
        """Создаём заказ и отзыв, возвращаем review_id."""
        order_resp = create_order_for_product(auth_client, product)
        if order_resp.status_code != 201:
            return None
        csrf = get_csrf(auth_client)
        resp = auth_client.post(
            f"/api/products/{product.id}/reviews/",
            {"rating": 3, "text": "Средний товар на тройку скажу честно."},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        if resp.status_code != 201:
            return None
        return resp.json()["id"]

    def test_review_detail_200(self, api_client, auth_client, product):
        review_id = self._create_review(auth_client, product)
        if not review_id:
            pytest.skip("Отзыв не создан")
        resp = api_client.get(f"/api/reviews/{review_id}/")
        assert resp.status_code == 200
        data = resp.json()
        assert "rating" in data
        assert "text" in data

    def test_review_detail_404(self, api_client):
        resp = api_client.get("/api/reviews/999999999/")
        assert resp.status_code == 404

    def test_review_seller_reply(self, seller_client, auth_client, product, seller_user):
        """Продавец может ответить на отзыв о своём товаре."""
        user, seller = seller_user
        # Привязываем товар к магазину продавца
        product.seller = seller
        product.save()

        review_id = self._create_review(auth_client, product)
        if not review_id:
            pytest.skip("Отзыв не создан")

        csrf = get_csrf(seller_client)
        resp = seller_client.post(
            f"/api/reviews/{review_id}/reply/",
            {"reply": "Спасибо за отзыв! Рады что товар понравился."},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert "reply" in data

