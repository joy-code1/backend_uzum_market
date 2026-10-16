"""
Тесты служебных эндпоинтов:
  GET /api/health
  GET /api/ping/
  GET /api/test/
  GET /api/hello/
  GET /api/hello/?name=...
"""

import pytest


# ─────────────────────────────── /api/health
@pytest.mark.django_db
class TestHealth:
    def test_health_returns_200(self, api_client, product):
        """Health-проба должна возвращать 200."""
        resp = api_client.get("/api/health")
        assert resp.status_code == 200

    def test_health_response_shape(self, api_client, product):
        """Проверяем все обязательные поля ответа."""
        resp = api_client.get("/api/health")
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "uzum-market-clone"
        assert "backend" in data
        assert "products" in data
        assert "time" in data

    def test_health_products_count(self, api_client, product):
        """Поле products должно отражать реальное количество товаров в БД."""
        from apps.products.models import Product

        count = Product.objects.count()
        resp = api_client.get("/api/health")
        assert resp.json()["products"] == count

    def test_health_time_format(self, api_client, product):
        """Поле time должно быть в ISO-формате UTC (YYYY-MM-DDTHH:MM:SS.mmmZ)."""
        import re

        resp = api_client.get("/api/health")
        time_value = resp.json()["time"]
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", time_value), (
            f"Неверный формат времени: {time_value}"
        )


# ─────────────────────────────── /api/ping/
@pytest.mark.django_db
class TestPing:
    def test_ping_returns_200(self, api_client):
        resp = api_client.get("/api/ping/")
        assert resp.status_code == 200

    def test_ping_response_fields(self, api_client):
        data = api_client.get("/api/ping/").json()
        assert data["status"] == "ok"
        assert data["ping"] == "pong"
        assert "message" in data
        assert "time" in data

    def test_ping_no_auth_required(self, api_client):
        """Ping должен быть доступен без авторизации."""
        resp = api_client.get("/api/ping/")
        assert resp.status_code == 200


# ─────────────────────────────── /api/test/
@pytest.mark.django_db
class TestServiceEndpoint:
    def test_get_returns_200(self, api_client):
        resp = api_client.get("/api/test/")
        assert resp.status_code == 200

    def test_get_response_fields(self, api_client):
        data = api_client.get("/api/test/").json()
        assert data["status"] == "success"
        assert "message" in data
        assert "backend" in data
        assert "environment" in data
        assert "time" in data

    def test_post_returns_200(self, api_client):
        resp = api_client.post("/api/test/", {"key": "value"}, format="json")
        assert resp.status_code == 200

    def test_post_echoes_data(self, api_client):
        """POST должен возвращать переданные данные в поле received_data."""
        payload = {"foo": "bar", "num": 42}
        data = api_client.post("/api/test/", payload, format="json").json()
        assert data["status"] == "success"
        assert data["received_data"] == payload


# ─────────────────────────────── /api/hello/
@pytest.mark.django_db
class TestHello:
    def test_hello_default_greeting(self, api_client):
        resp = api_client.get("/api/hello/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "Гость" in data["greeting"]

    def test_hello_custom_name(self, api_client):
        resp = api_client.get("/api/hello/?name=Шахзод")
        assert resp.status_code == 200
        assert "Шахзод" in resp.json()["greeting"]

    def test_hello_has_message(self, api_client):
        data = api_client.get("/api/hello/").json()
        assert "message" in data
        assert len(data["message"]) > 0

