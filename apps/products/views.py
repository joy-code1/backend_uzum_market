"""Каталог (§5.2, §5.3, §5.4 ТЗ): категории, продавцы, товары, отзывы.

Витрина отдаёт только active; draft/archived видны владельцу магазина.
`next`/`previous` в пагинации — boolean, фасеты считаются до своих фильтров.
"""

import logging

from django.db.models import Case, Count, F, FloatField, Max, Min, Q, Value, When
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import api_view

from apps.core.cache import cache_private, cache_public
from apps.core.pagination import EnvelopePagination
from apps.orders.services import purchased_qty_map
from apps.users.throttling import ScopedUserOrIpThrottle

from .models import Category, Product, Review, Seller
from .serializers import (
    CategorySerializer,
    ProductSerializer,
    ProductWriteSerializer,
    ReviewSerializer,
    SellerSerializer,
)
from .services import (
    active_seller_product_counts,
    owns_product,
    recompute_product_reviews,
    upsert_review,
)

logger = logging.getLogger(__name__)


def get_object_or_404_or_none(model_or_qs, **kwargs):
    qs = model_or_qs.objects.all() if hasattr(model_or_qs, "objects") else model_or_qs
    try:
        return qs.get(**kwargs)
    except qs.model.DoesNotExist:
        return None

@api_view(['GET'])
def getProducts(request):
    products = Product.objects.all()
    serializer = ProductSerializer(products, many=True)
    return Response(serializer.data)


# --------------------------------------------------------------------- категории
class CategoriesView(APIView):
    """GET /api/categories/ — конверт, product_count по активным товарам."""

    serializer_class = serializers.Serializer

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(tags=["categories"], responses={200: None})
    def get(self, request):
        qs = Category.objects.annotate(
            product_count=Count("products", filter=Q(products__status=Product.Status.ACTIVE))
        ).order_by("name")
        data = CategorySerializer(qs, many=True).data
        return cache_public(Response(EnvelopePagination.whole_list(data)), request)


# ----------------------------------------------------------------------- продавцы
class SellersView(APIView):
    """GET /api/sellers/ — рейтинг ↓, затем число товаров ↓ (§5.2 ТЗ)."""

    serializer_class = serializers.Serializer

    permission_classes = [permissions.AllowAny]
    throttle_classes = []

    @extend_schema(tags=["sellers"], operation_id="sellers_list", responses={200: None})
    def get(self, request):
        from apps.orders.models import Order

        qs = Seller.objects.annotate(
            product_count=Count("products", filter=Q(products__status=Product.Status.ACTIVE), distinct=True),
            order_count=Count(
                "order_items__order",
                filter=~Q(order_items__order__status=Order.Status.CANCELLED),
                distinct=True,
            ),
        ).order_by("-rating", "-product_count", "id")
        data = SellerSerializer(qs, many=True, context={"request": request}).data
        return cache_public(Response(EnvelopePagination.whole_list(data)), request)


class SellerDetailView(APIView):
    """GET /api/sellers/{id_or_slug}/ — Seller + активные товары магазина."""

    serializer_class = serializers.Serializer

    permission_classes = [permissions.AllowAny]
    throttle_classes = []

    @extend_schema(tags=["sellers"], responses={200: None, 404: None})
    def get(self, request, id_or_slug: str):
        seller = resolve_seller(id_or_slug)
        if seller is None:
            raise Http404("Магазин не найден.")
        seller_counts = active_seller_product_counts()
        data = SellerSerializer(
            seller,
            context={
                "request": request,
                "seller_counts": {
                    seller.pk: {
                        "product_count": seller_counts.get(seller.pk, 0),
                        "order_count": seller_order_count(seller),
                    }
                },
            },
        ).data
        products = (
            Product.objects.filter(seller=seller, status=Product.Status.ACTIVE)
            .select_related("seller", "category")
            .order_by("-is_ad", "-rating", "-reviews_count", "id")
        )
        data["products"] = ProductSerializer(products, many=True, context=_product_context(request, products)).data
        return cache_public(Response(data), request)


def seller_order_count(seller) -> int:
    from apps.orders.models import Order, OrderItem

    return (
        OrderItem.objects.filter(seller=seller)
        .exclude(order__status=Order.Status.CANCELLED)
        .values("order")
        .distinct()
        .count()
    )


def resolve_seller(id_or_slug: str):
    if id_or_slug.isdigit():
        return get_object_or_404_or_none(Seller, pk=int(id_or_slug))
    return get_object_or_404_or_none(Seller, slug=id_or_slug)


# -------------------------------------------------------------------------- товары
PRODUCT_ORDERINGS = {
    "price": ("price", "id"),
    "-price": ("-price", "id"),
    "rating": ("rating", "id"),
    "-rating": ("-rating", "-reviews_count", "id"),
    "new": ("-created_at", "-id"),
    "-created_at": ("-created_at", "-id"),
    "popular": ("-views", "-reviews_count", "id"),
    "discount": ("-discount_expr", "-id"),
}


class ProductsView(APIView):
    """GET /api/products/ — витрина с фильтрами/фасетами; POST — создать товар (§5.3 ТЗ)."""

    serializer_class = ProductWriteSerializer
    permission_classes = [permissions.AllowAny]

    def get_throttles(self):
        if self.request.method == "POST":
            self.throttle_scope = "product_write"
            return [ScopedUserOrIpThrottle()]
        return []

    @extend_schema(tags=["products"], operation_id="products_list", responses={200: None})
    def get(self, request):
        qs = Product.objects.select_related("seller", "category")

        # ids=1,2,3 — вернуть эти товары (active + собственные), фильтры игнорируются.
        raw_ids = (request.query_params.get("ids") or "").strip()
        if raw_ids:
            try:
                ids = [int(x) for x in raw_ids.split(",") if x.strip()]
            except ValueError:
                ids = []
                qs = qs.none()
            if ids:
                qs = qs.filter(Q(id__in=ids) & visible_q(request))
            return self._paginated(request, qs)

        status_param = (request.query_params.get("status") or "").strip()
        if status_param in Product.Status.values:
            # Чужие draft/archived не отдаются: постороннему — пусто, владельцу — свои (§5.3 ТЗ).
            own_seller = user_shop_id(request.user)
            if status_param != Product.Status.ACTIVE and own_seller is None:
                qs = qs.none()
            elif status_param != Product.Status.ACTIVE:
                qs = qs.filter(status=status_param, seller_id=own_seller)
            else:
                qs = qs.filter(status=Product.Status.ACTIVE)
        elif status_param:
            qs = qs.none()  # неизвестный статус → пустой список
        else:
            qs = qs.filter(status=Product.Status.ACTIVE)

        q = (request.query_params.get("q") or request.query_params.get("search") or "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q) | Q(brand__icontains=q))

        seller = (request.query_params.get("seller") or "").strip()
        if seller:
            if seller.isdigit():
                qs = qs.filter(seller_id=int(seller))
            else:
                qs = qs.filter(seller__slug=seller)

        # Фасет категорий — без учёта фильтра по категории (§5.3 ТЗ).
        request._facet_categories_base = qs
        category = (request.query_params.get("category") or "").strip()
        if category:
            qs = qs.filter(category_ref(category))

        # Фасет цены — до фильтра по цене (§5.3 ТЗ).
        request._facet_price = qs.aggregate(min=Min("price"), max=Max("price"))
        min_price = parse_int(request.query_params.get("min_price"))
        max_price = parse_int(request.query_params.get("max_price"))
        if min_price is not None:
            qs = qs.filter(price__gte=min_price)
        if max_price is not None:
            qs = qs.filter(price__lte=max_price)

        min_rating = parse_float(request.query_params.get("min_rating"))
        if min_rating is not None:
            qs = qs.filter(rating__gte=min_rating)
        if truthy(request.query_params.get("discounted")):
            qs = qs.filter(old_price__gt=F("price"))
        if truthy(request.query_params.get("in_stock")):
            qs = qs.filter(stock__gt=0)

        ordering = (request.query_params.get("ordering") or "").strip()
        if ordering == "discount":
            qs = qs.annotate(
                discount_expr=Case(
                    When(old_price__gt=F("price"), then=(F("old_price") - F("price")) * 100.0 / F("old_price")),
                    default=Value(0),
                    output_field=FloatField(),
                )
            )
        order_keys = PRODUCT_ORDERINGS.get(ordering)
        if order_keys:
            qs = qs.order_by(*order_keys)
        else:
            # «Рекомендованное»: стабильный скоринг — реклама, рейтинг, популярность (§5.3 ТЗ).
            qs = qs.order_by("-is_ad", "-rating", "-reviews_count", "-views", "id")

        return self._paginated(request, qs)

    @extend_schema(tags=["products"], request=ProductWriteSerializer, responses={201: None, 403: None})
    def post(self, request):
        shop = require_shop(request)
        serializer = ProductWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.save(seller=shop)
        detail = "Товар сохранён в черновиках" if product.status == Product.Status.DRAFT else "Товар опубликован"
        return Response({"id": product.pk, "detail": detail}, status=status.HTTP_201_CREATED)

    def _paginated(self, request, qs):
        paginator = EnvelopePagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ProductSerializer(page, many=True, context=_product_context(request, page or []))
        payload = paginator.envelope(paginator.page, serializer.data)
        facets = build_facets(request, page, serializer.data)
        if facets is not None:
            payload["facets"] = facets
        return cache_public(Response(payload), request)


def user_shop_id(user) -> int | None:
    """pk магазина пользователя (reverse OneToOne: атрибута shop_id у User нет)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    shop = getattr(user, "shop", None)
    return shop.pk if shop else None


def visible_q(request):
    """Витрина: active всем; draft/archived — только владельцу магазина."""
    shop_id = user_shop_id(getattr(request, "user", None))
    if shop_id:
        return Q(status=Product.Status.ACTIVE) | Q(seller_id=shop_id)
    return Q(status=Product.Status.ACTIVE)


def category_ref(category: str):
    if category.isdigit():
        return Q(category_id=int(category))
    return Q(category__slug=category)


def parse_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def build_facets(request, page, results):
    base = getattr(request, "_facet_categories_base", None)
    if base is None:
        return None
    price = getattr(request, "_facet_price", None) or {}
    categories = (
        base.values("category_id", "category__name", "category__slug", "category__emoji", "category__color")
        .annotate(n=Count("id"))
        .order_by("category__name")
    )
    return {
        "price": {
            "min": int(price.get("min") or 0),
            "max": int(price.get("max") or 0),
        },
        "categories": [
            {
                "id": row["category_id"],
                "name": row["category__name"],
                "slug": row["category__slug"],
                "emoji": row["category__emoji"],
                "color": row["category__color"],
                "product_count": row["n"],
            }
            for row in categories
            if row["category_id"] is not None
        ],
    }


def _product_context(request, products):
    """Контекст для списка товаров: счётчики продавцов и «мой отзыв» одним запросом."""
    seller_ids = {p.seller_id for p in products}
    product_counts = active_seller_product_counts() if seller_ids else {}
    seller_counts = {sid: {"product_count": product_counts.get(sid, 0)} for sid in seller_ids}
    reviewed_ids = None
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        reviewed_ids = set(
            Review.objects.filter(user=user, product_id__in=[p.pk for p in products]).values_list(
                "product_id", flat=True
            )
        )
    return {
        "request": request,
        "seller_counts": seller_counts,
        "reviewed_product_ids": reviewed_ids,
    }


def require_shop(request):
    """401 анониму, 403 — аккаунту без магазина (§5.3 ТЗ)."""
    user = request.user
    if user is None or not user.is_authenticated:
        raise NotAuthenticated()
    shop = getattr(user, "shop", None)
    if shop is None:
        raise PermissionDenied("Сначала создайте магазин, чтобы добавлять товары.")
    return shop


def require_authenticated(request):
    if request.user is None or not request.user.is_authenticated:
        raise NotAuthenticated()


def resolve_product(id_or_slug: str):
    if id_or_slug.isdigit():
        return get_object_or_404_or_none(Product.objects.select_related("seller", "category"), pk=int(id_or_slug))
    return get_object_or_404_or_none(Product.objects.select_related("seller", "category"), slug=id_or_slug)


class ProductDetailView(APIView):
    """GET/PATCH/DELETE /api/products/{id_or_slug}/ (§5.3 ТЗ)."""

    permission_classes = [permissions.AllowAny]

    def get_throttles(self):
        if self.request.method in ("POST", "PUT", "PATCH", "DELETE"):
            self.throttle_scope = "product_write"
            return [ScopedUserOrIpThrottle()]
        return []

    @extend_schema(tags=["products"], responses={200: None, 404: None})
    def get(self, request, id_or_slug: str):
        product = resolve_product(id_or_slug)
        if product is None:
            raise Http404("Товар не найден.")
        if product.status != Product.Status.ACTIVE and not owns_product(request.user, product):
            raise Http404("Товар не найден.")
        data = ProductSerializer(product, context=_product_context(request, [product])).data
        if product.status == Product.Status.ACTIVE:
            return cache_public(Response(data), request)
        return cache_private(Response(data), request)

    @extend_schema(tags=["products"], request=ProductWriteSerializer, responses={200: None, 403: None, 404: None})
    def patch(self, request, id_or_slug: str):
        require_authenticated(request)
        product = resolve_product(id_or_slug)
        if product is None:
            raise Http404("Товар не найден.")
        if not owns_product(request.user, product):
            raise PermissionDenied("Это товар другого магазина.")
        serializer = ProductWriteSerializer(product, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        product = serializer.save()
        return Response({"id": product.pk, "detail": "Изменения сохранены"})

    @extend_schema(tags=["products"], responses={200: None, 403: None, 404: None})
    def delete(self, request, id_or_slug: str):
        require_authenticated(request)
        product = resolve_product(id_or_slug)
        if product is None:
            raise Http404("Товар не найден.")
        if not owns_product(request.user, product):
            raise PermissionDenied("Это товар другого магазина.")
        product.delete()  # отзывы удаляются каскадом (FK CASCADE)
        return Response({"detail": "Товар удалён"})


class ProductStatusView(APIView):
    """POST /api/products/{id}/status/ — смена статуса (идемпотентна, §6.5 ТЗ)."""

    serializer_class = serializers.Serializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedUserOrIpThrottle]
    throttle_scope = "product_write"

    @extend_schema(tags=["products"], responses={200: None, 400: None, 403: None, 404: None})
    def post(self, request, id_or_slug: str):
        require_authenticated(request)
        product = resolve_product(id_or_slug)
        if product is None:
            raise Http404("Товар не найден.")
        if not owns_product(request.user, product):
            raise PermissionDenied("Это товар другого магазина.")
        new_status = (request.data.get("status") or "").strip() if isinstance(request.data, dict) else ""
        if new_status not in Product.Status.values:
            return Response(
                {
                    "detail": "Статус может быть active, draft или archived.",
                    "fields": {"status": "active, draft или archived."},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if product.status != new_status:
            product.status = new_status
            product.save(update_fields=["status", "updated_at"])
        return Response({"detail": "Статус обновлён"})


class ProductViewCounterView(APIView):
    """POST /api/products/{id}/view/ — инкремент просмотров, без авторизации (§5.3 ТЗ)."""

    serializer_class = serializers.Serializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(tags=["products"], responses={200: None, 404: None})
    def post(self, request, id_or_slug: str):
        product = resolve_product(id_or_slug)
        if product is None:
            raise Http404("Товар не найден.")
        Product.objects.filter(pk=product.pk).update(views=F("views") + 1)
        return Response({"ok": True})


class ProductMineView(APIView):
    """GET /api/products/mine/ — свои товары, включая draft/archived (§5.3 ТЗ)."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=["products"], responses={200: None, 401: None})
    def get(self, request):
        shop = getattr(request.user, "shop", None)
        if shop is None:
            payload = EnvelopePagination.whole_list([])
            payload["detail"] = "У вас пока нет магазина"
            return cache_private(Response(payload), request)
        qs = Product.objects.filter(seller=shop).select_related("seller", "category").order_by("-created_at", "-id")
        paginator = EnvelopePagination()
        page = paginator.paginate_queryset(qs, request)
        data = ProductSerializer(page, many=True, context=_product_context(request, page or [])).data
        return cache_private(Response(paginator.envelope(paginator.page, data)), request)


# -------------------------------------------------------------------------- отзывы
class ProductReviewsView(APIView):
    """GET/POST /api/products/{id}/reviews/ (§5.4 ТЗ)."""

    serializer_class = serializers.Serializer
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=["reviews"], responses={200: None, 404: None})
    def get(self, request, id_or_slug: str):
        product = resolve_product(id_or_slug)
        if product is None:
            raise Http404("Товар не найден.")
        if product.status != Product.Status.ACTIVE and not owns_product(request.user, product):
            raise Http404("Товар не найден.")
        reviews = list(product.reviews.select_related("user").order_by("-created_at", "-id"))
        results = ReviewSerializer(reviews, many=True, context={"request": request}).data

        # verified — «есть не-отменённый заказ с этим товаром» (§2 ТЗ): пересчитываем
        # одним пакетным запросом, чтобы отмена заказа сразу же делала его ложным.
        verified_map = batch_verified_map(product, [r.user_id for r in reviews if r.user_id])
        for review, serialized in zip(reviews, results, strict=False):
            if review.user_id:
                serialized["verified"] = review.user_id in verified_map

        count = len(results)
        breakdown = [
            {"stars": stars, "count": sum(1 for r in results if r["rating"] == stars)} for stars in (5, 4, 3, 2, 1)
        ]
        average = round(sum(r["rating"] for r in results) / count, 1) if count else 0.0
        purchases_map = purchased_qty_map(request.user, [product.pk])
        purchases = purchases_map.get(product.pk, 0)
        can_review = bool(request.user.is_authenticated and purchases > 0)
        return Response(
            {
                "summary": {"count": count, "average": average, "breakdown": breakdown},
                "results": results,
                "can_review": can_review,
                "purchases": purchases,
            }
        )

    @extend_schema(tags=["reviews"], responses={201: None, 200: None, 403: None, 404: None})
    def post(self, request, id_or_slug: str):
        require_authenticated(request)
        product = resolve_product(id_or_slug)
        if product is None:
            raise Http404("Товар не найден.")
        if product.status != Product.Status.ACTIVE:
            raise Http404("Товар не найден.")

        existing = Review.objects.filter(product=product, user=request.user).first()
        has_purchase = purchased_qty_map(request.user, [product.pk]).get(product.pk, 0) > 0
        if existing is None and not has_purchase:
            # Шлюз покупки (§5.4 ТЗ): новый отзыв — только купившим; свой можно править всегда.
            raise PermissionDenied(
                "Отзыв могут оставить только покупатели, которые уже купили этот товар. "
                "Оформите заказ — и поделитесь мнением."
            )

        data = request.data if isinstance(request.data, dict) else {}
        rating = data.get("rating")
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Оценка — целое число от 1 до 5.", "fields": {"rating": "Целое число от 1 до 5."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not (1 <= rating <= 5):
            return Response(
                {"detail": "Оценка — целое число от 1 до 5.", "fields": {"rating": "Целое число от 1 до 5."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        text = (data.get("text") or "").strip()
        if not (15 <= len(text) <= 2000):
            return Response(
                {"detail": "Текст отзыва — от 15 до 2000 символов.", "fields": {"text": "От 15 до 2000 символов."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        pros = (data.get("pros") or "").strip()[:200]
        cons = (data.get("cons") or "").strip()[:200]

        review, created = upsert_review(
            product=product, user=request.user, rating=rating, text=text, pros=pros, cons=cons
        )
        if created:
            return Response(
                {"id": review.pk, "updated": False, "detail": "Спасибо за отзыв!"},
                status=status.HTTP_201_CREATED,
            )
        return Response({"id": review.pk, "updated": True, "detail": "Отзыв обновлён"})


def batch_verified_map(product, user_ids):
    if not user_ids:
        return set()
    from apps.orders.models import Order, OrderItem

    rows = (
        OrderItem.objects.filter(product=product, order__user_id__in=user_ids)
        .exclude(order__status=Order.Status.CANCELLED)
        .values_list("order__user_id", flat=True)
        .distinct()
    )
    return set(rows)


class ReviewDetailView(APIView):
    """DELETE /api/reviews/{id}/ — автор или владелец магазина (§5.4 ТЗ)."""

    serializer_class = serializers.Serializer
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=["reviews"], responses={200: None, 403: None, 404: None})
    def delete(self, request, review_id: int):
        require_authenticated(request)
        review = get_object_or_404_or_none(Review.objects.select_related("product"), pk=review_id)
        if review is None:
            raise Http404("Отзыв не найден.")
        is_author = review.user_id == request.user.pk if request.user.is_authenticated else False
        if not is_author and not owns_product(request.user, review.product):
            raise PermissionDenied("Удалить отзыв может его автор или владелец магазина.")
        product = review.product
        review.delete()
        recompute_product_reviews(product)
        return Response({"detail": "Отзыв удалён"})


class ReviewReplyView(APIView):
    """POST /api/reviews/{id}/reply/ — ответ продавца товара (§5.4 ТЗ)."""

    serializer_class = serializers.Serializer
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=["reviews"], responses={200: None, 400: None, 403: None, 404: None})
    def post(self, request, review_id: int):
        require_authenticated(request)
        review = get_object_or_404_or_none(Review.objects.select_related("product"), pk=review_id)
        if review is None:
            raise Http404("Отзыв не найден.")
        if not owns_product(request.user, review.product):
            raise PermissionDenied("Ответить может только владелец магазина товара.")
        reply = (request.data.get("reply") or "").strip() if isinstance(request.data, dict) else ""
        if not (5 <= len(reply) <= 800):
            return Response(
                {"detail": "Ответ — от 5 до 800 символов.", "fields": {"reply": "От 5 до 800 символов."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        review.seller_reply = reply
        review.save(update_fields=["seller_reply", "updated_at"])
        return Response({"detail": "Ответ опубликован"})
