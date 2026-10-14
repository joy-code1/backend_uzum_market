
from django.urls import path

from .views import (
    CategoriesView,
    ProductDetailView,
    ProductMineView,
    ProductReviewsView,
    ProductStatusView,
    ProductsView,
    ProductViewCounterView,
    ReviewDetailView,
    ReviewReplyView,
    SellerDetailView,
    SellersView,
    getProducts,
)

urlpatterns = [
    # Главный роут приложения (например, http://ip:8000/api/v1/)
    path("", getProducts, name="get-products"),

    # Роуты товаров
    path("products/", ProductsView.as_view(), name="products"),
    path("products/mine/", ProductMineView.as_view(), name="product-mine"),
    path("products/<str:id_or_slug>/", ProductDetailView.as_view(), name="product-detail"),
    path("products/<str:id_or_slug>/status/", ProductStatusView.as_view(), name="product-status"),
    path("products/<str:id_or_slug>/view/", ProductViewCounterView.as_view(), name="product-view"),
    path("products/<str:id_or_slug>/reviews/", ProductReviewsView.as_view(), name="product-reviews"),

    # Категории и продавцы
    path("categories/", CategoriesView.as_view(), name="categories"),
    path("sellers/", SellersView.as_view(), name="sellers"),
    path("sellers/<str:id_or_slug>/", SellerDetailView.as_view(), name="seller-detail"),

    # Отзывы
    path("reviews/<int:review_id>/", ReviewDetailView.as_view(), name="review-detail"),
    path("reviews/<int:review_id>/reply/", ReviewReplyView.as_view(), name="review-reply"),
]