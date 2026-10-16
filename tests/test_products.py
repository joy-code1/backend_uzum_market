"""
Тесты API каталога:
  GET /api/products/          — список товаров (фильтры, пагинация)
  GET /api/products/{id}/     — карточка товара
  GET /api/categories/        — список категорий
  GET /api/sellers/           — список продавцов
  GET /api/sellers/{slug}/    — карточка продавца
  POST/PATCH/DELETE /api/products/  — CRUD (только продавец)
"""

import pytest

from apps.products.models import Category, Product, Seller

PRODUCTS_URL = "/api/products/"
CATEGORIES_URL = "/api/categories/"
SELLERS_URL = "/api/sellers/"

# Все поля, которые должны быть в карточке товара согласно ТЗ
REQUIRED_PRODUCT_FIELDS = {
    "id",
    "slug",
    "title",
    "description",
    "price",
    "old_price",
    "discount_percent",
    "monthly_payment",
    "rating",
    "reviews_count",
    "rating_breakdown",
    "delivery_time",
    "stock",
    "in_stock",
    "brand",
    "image",
    "images",
    "characteristics",
    "is_ad",
    "views",
    "status",
    "created_at",
    "updated_at",
    "seller",
    "category",
    "has_own_review",
}


# ─────────────────────────────── Категории
@pytest.mark.django_db
class TestCategories:
    def test_categories_200(self, api_client, category):
        resp = api_client.get(CATEGORIES_URL)
        assert resp.status_code == 200

    def test_categories_envelope_shape(self, api_client, category):
        """Ответ должен быть envelope-форматом с полями пагинации."""
        data = api_client.get(CATEGORIES_URL).json()
        required_keys = {"count", "next", "previous", "results", "page", "page_size", "total_pages"}
        assert required_keys.issubset(data.keys()), f"Отсутствуют поля: {required_keys - data.keys()}"

    def test_categories_next_previous_are_bool(self, api_client, category):
        data = api_client.get(CATEGORIES_URL).json()
        assert isinstance(data["next"], bool)
        assert isinstance(data["previous"], bool)

    def test_category_fields(self, api_client, category):
        data = api_client.get(CATEGORIES_URL).json()
        item = data["results"][0]
        expected_fields = {"id", "name", "slug", "emoji", "color", "product_count"}
        assert expected_fields.issubset(item.keys())

    def test_category_product_count_is_int(self, api_client, category):
        data = api_client.get(CATEGORIES_URL).json()
        for item in data["results"]:
            assert isinstance(item["product_count"], int)

    def test_category_product_count_active_only(self, api_client, category, seller):
        """product_count считает только активные товары."""
        Product.objects.create(
            title="Активный товар", slug="active-product",
            description="Описание длиннее двадцати символов ровно.",
            price=100_000, stock=5, category=category, seller=seller,
            status=Product.Status.ACTIVE,
        )
        Product.objects.create(
            title="Черновик товар", slug="draft-product",
            description="Описание черновика длиннее двадцати символов.",
            price=200_000, stock=5, category=category, seller=seller,
            status=Product.Status.DRAFT,
        )
        data = api_client.get(CATEGORIES_URL).json()
        cat = next(c for c in data["results"] if c["slug"] == "elektronika")
        assert cat["product_count"] == 1  # только активный


# ─────────────────────────────── Товары — список
@pytest.mark.django_db
class TestProductsList:
    def test_products_200(self, api_client):
        resp = api_client.get(PRODUCTS_URL)
        assert resp.status_code == 200

    def test_products_envelope_shape(self, api_client, product):
        data = api_client.get(PRODUCTS_URL).json()
        required_keys = {"count", "next", "previous", "results", "page", "page_size", "total_pages"}
        assert required_keys.issubset(data.keys())

    def test_products_contain_active_only(self, api_client, product, category, seller):
        """Витрина должна показывать только активные товары."""
        Product.objects.create(
            title="Черновик скрытый",
            slug="hidden-draft",
            description="Длинное описание черновика, больше двадцати символов.",
            price=50_000,
            stock=3,
            category=category,
            seller=seller,
            status=Product.Status.DRAFT,
        )
        data = api_client.get(PRODUCTS_URL).json()
        slugs = [p["slug"] for p in data["results"]]
        assert "test-samsung-galaxy" in slugs
        assert "hidden-draft" not in slugs

    def test_products_required_fields(self, api_client, product):
        data = api_client.get(PRODUCTS_URL).json()
        assert len(data["results"]) > 0
        item = data["results"][0]
        missing = REQUIRED_PRODUCT_FIELDS - item.keys()
        assert not missing, f"Отсутствуют поля: {missing}"

    def test_products_filter_by_category(self, api_client, product, seller):
        """Фильтр ?category должен возвращать только товары нужной категории."""
        other_cat = Category.objects.create(name="Одежда", slug="odezhda")
        Product.objects.create(
            title="Рубашка тестовая",
            slug="test-shirt",
            description="Тестовая рубашка для проверки фильтра категорий.",
            price=150_000,
            stock=20,
            category=other_cat,
            seller=seller,
            status=Product.Status.ACTIVE,
        )
        data = api_client.get(f"{PRODUCTS_URL}?category=elektronika").json()
        assert all(p["category"]["slug"] == "elektronika" for p in data["results"])

    def test_products_filter_by_price_min(self, api_client, product, category, seller):
        Product.objects.create(
            title="Дешёвый товар тест",
            slug="cheap-product-test",
            description="Описание дешёвого товара для фильтра по цене.",
            price=10_000,
            stock=5,
            category=category,
            seller=seller,
            status=Product.Status.ACTIVE,
        )
        data = api_client.get(f"{PRODUCTS_URL}?price_min=1000000").json()
        for p in data["results"]:
            assert p["price"] >= 1_000_000

    def test_products_search(self, api_client, product):
        """Поиск ?q должен находить товары по названию."""
        data = api_client.get(f"{PRODUCTS_URL}?q=Samsung").json()
        assert any("Samsung" in p["title"] for p in data["results"])

    def test_products_pagination(self, api_client, category, seller):
        """Пагинация должна работать: ?page_size=2 возвращает не более 2 товаров."""
        for i in range(5):
            Product.objects.create(
                title=f"Товар пагинация {i}",
                slug=f"pagination-product-{i}",
                description="Описание товара для проверки пагинации списка.",
                price=100_000 + i * 1000,
                stock=i + 1,
                category=category,
                seller=seller,
                status=Product.Status.ACTIVE,
            )
        data = api_client.get(f"{PRODUCTS_URL}?page_size=2").json()
        assert len(data["results"]) <= 2


# ─────────────────────────────── Товар — детальная карточка
@pytest.mark.django_db
class TestProductDetail:
    def test_product_detail_by_id(self, api_client, product):
        resp = api_client.get(f"{PRODUCTS_URL}{product.id}/")
        assert resp.status_code == 200

    def test_product_detail_by_slug(self, api_client, product):
        resp = api_client.get(f"{PRODUCTS_URL}{product.slug}/")
        assert resp.status_code == 200

    def test_product_detail_all_required_fields(self, api_client, product):
        data = api_client.get(f"{PRODUCTS_URL}{product.id}/").json()
        missing = REQUIRED_PRODUCT_FIELDS - data.keys()
        assert not missing, f"Отсутствуют поля: {missing}"

    def test_product_detail_correct_data(self, api_client, product):
        data = api_client.get(f"{PRODUCTS_URL}{product.id}/").json()
        assert data["title"] == product.title
        assert data["price"] == product.price
        assert data["slug"] == product.slug

    def test_product_detail_404_unknown(self, api_client):
        resp = api_client.get(f"{PRODUCTS_URL}999999999/")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_product_discount_percent(self, api_client, product):
        """discount_percent должен быть > 0 для товара с old_price > price."""
        data = api_client.get(f"{PRODUCTS_URL}{product.id}/").json()
        # product имеет price=3_500_000, old_price=4_000_000
        assert data["discount_percent"] > 0

    def test_product_in_stock_true(self, api_client, product):
        data = api_client.get(f"{PRODUCTS_URL}{product.id}/").json()
        assert data["in_stock"] is True  # stock=10

    def test_product_in_stock_false(self, api_client, category, seller):
        out_of_stock = Product.objects.create(
            title="Нет в наличии продукт",
            slug="out-of-stock-product",
            description="Этот товар закончился на складе давно.",
            price=200_000,
            stock=0,
            category=category,
            seller=seller,
            status=Product.Status.ACTIVE,
        )
        data = api_client.get(f"{PRODUCTS_URL}{out_of_stock.id}/").json()
        assert data["in_stock"] is False


# ─────────────────────────────── Продавцы
@pytest.mark.django_db
class TestSellers:
    def test_sellers_200(self, api_client, seller):
        resp = api_client.get(SELLERS_URL)
        assert resp.status_code == 200

    def test_sellers_envelope(self, api_client, seller):
        data = api_client.get(SELLERS_URL).json()
        assert "results" in data
        assert "count" in data

    def test_sellers_required_fields(self, api_client, seller):
        data = api_client.get(SELLERS_URL).json()
        assert len(data["results"]) > 0
        item = data["results"][0]
        required = {"id", "name", "slug", "city", "rating", "product_count", "verified"}
        missing = required - item.keys()
        assert not missing, f"Отсутствуют поля: {missing}"

    def test_seller_detail_200(self, api_client, seller):
        resp = api_client.get(f"{SELLERS_URL}{seller.slug}/")
        assert resp.status_code == 200

    def test_seller_detail_404(self, api_client):
        resp = api_client.get(f"{SELLERS_URL}nonexistent-seller-slug/")
        assert resp.status_code == 404

    def test_seller_detail_correct_data(self, api_client, seller):
        data = api_client.get(f"{SELLERS_URL}{seller.slug}/").json()
        assert data["slug"] == seller.slug
        assert data["name"] == seller.name


# ─────────────────────────────── CRUD товаров (продавец)
@pytest.mark.django_db
class TestProductCRUD:
    def _get_csrf(self, client):
        resp = client.get("/api/auth/csrf/")
        return resp.json()["csrf"]

    def test_create_product_as_seller(self, seller_client, category, seller_user):
        user, seller = seller_user
        csrf = self._get_csrf(seller_client)
        payload = {
            "title": "Новый товар от продавца",
            "description": "Детальное описание нового товара длиннее двадцати символов.",
            "price": 500_000,
            "stock": 15,
            "category_id": category.id,
            "delivery_time": "1–3 дня",
            "brand": "TestBrand",
            "images": ["/products/gen/a-1.svg"],
            "characteristics": {"Вес": "1 кг"},
        }
        resp = seller_client.post(
            PRODUCTS_URL,
            payload,
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == payload["title"]
        assert data["price"] == payload["price"]

    def test_create_product_anonymous_401(self, api_client, category):
        csrf = self._get_csrf(api_client)
        resp = api_client.post(
            PRODUCTS_URL,
            {
                "title": "Анонимный товар",
                "description": "Описание длиннее двадцати символов тут.",
                "price": 100_000,
                "stock": 1,
                "category_id": category.id,
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 401

    def test_delete_own_product(self, seller_client, category, seller_user):
        user, seller = seller_user
        # Создаём товар
        csrf = self._get_csrf(seller_client)
        create_resp = seller_client.post(
            PRODUCTS_URL,
            {
                "title": "Товар для удаления",
                "description": "Описание товара который мы будем удалять сейчас.",
                "price": 300_000,
                "stock": 2,
                "category_id": category.id,
                "delivery_time": "Завтра",
                "brand": "Brand",
                "images": ["/products/gen/a-1.svg"],
                "characteristics": {},
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert create_resp.status_code == 201
        product_id = create_resp.json()["id"]

        # Удаляем
        del_resp = seller_client.delete(
            f"{PRODUCTS_URL}{product_id}/",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert del_resp.status_code == 204
        assert not Product.objects.filter(id=product_id).exists()

    def test_delete_other_seller_product_403(self, auth_client, product):
        """Покупатель не может удалить чужой товар."""
        csrf = self._get_csrf(auth_client)
        resp = auth_client.delete(
            f"{PRODUCTS_URL}{product.id}/",
            HTTP_X_CSRFTOKEN=csrf,
        )
        # 403 или 404 — оба допустимы в зависимости от реализации
        assert resp.status_code in (403, 404)

