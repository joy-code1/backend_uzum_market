"""Сид демо-данных (§9 ТЗ).

    python manage.py seed              # залить, если БД пуста (иначе выйти)
    python manage.py seed --force      # залить даже если данные есть (добавляет недостающее)
    python manage.py seed --reset      # стереть данные каталога/заказов и залить заново

POST /api/demo/reset/ вызывает этот же код с reset=True.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.orders.models import Order, OrderEvent, OrderItem, PromoCode
from apps.orders.services import calc_totals, generate_number

from ... import dataset as _dataset
from ...dataset import CATEGORIES, DEMO_ORDERS, DEMO_PASSWORD, DEMO_USERS, DRAFT_PRODUCT, PRODUCTS, PROMO_CODES
from ...gen_media import write_svg
from ...models import Category, Product, Review, Seller
from ...services import recompute_product_reviews, recompute_seller_reviews
from ...translit import unique_slug

REVIEWS = _dataset.reviews()

User = get_user_model()

EMOJI_BY_CATEGORY = {slug: emoji for _, slug, emoji, _ in CATEGORIES}


class Command(BaseCommand):
    help = "Демо-данные маркетплейса: категории, магазины, товары, отзывы, заказы"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="стереть данные и залить сид")
        parser.add_argument("--force", action="store_true", help="заливать, даже если данные уже есть")

    def handle(self, *args, **options):
        reset = options["reset"]
        force = options["force"]

        if reset:
            self._wipe()
        elif not force and (Product.objects.exists() or Category.objects.exists()):
            self.stdout.write("Демо-данные уже есть — пропускаю (seed --reset, чтобы пересоздать).")
            return

        with transaction.atomic():
            self._seed()
        self.stdout.write(self.style.SUCCESS("Демо-данные готовы."))

    def _wipe(self):
        # Сессии и суперюзеров не трогаем: сброс — про демо-контент.
        OrderItem.objects.all().delete()
        OrderEvent.objects.all().delete()
        Order.objects.all().delete()
        Review.objects.all().delete()
        Product.objects.all().delete()
        Seller.objects.all().delete()
        Category.objects.all().delete()
        PromoCode.objects.all().delete()
        try:
            from apps.uploads.models import MediaFile

            MediaFile.objects.all().delete()
        except Exception:  # noqa: S110
            pass
        User.objects.filter(is_staff=False, is_superuser=False).delete()
        self.stdout.write("Старые данные удалены.")

    def _seed(self):
        now = timezone.now()

        categories = {}
        for name, slug, emoji, color in CATEGORIES:
            cat, _ = Category.objects.update_or_create(
                slug=slug, defaults={"name": name, "emoji": emoji, "color": color}
            )
            categories[slug] = cat

        sellers = {}
        for name, slug, city, description, verified in _dataset.SELLERS:
            sel, _ = Seller.objects.update_or_create(
                slug=slug, defaults={"name": name, "city": city, "description": description, "verified": verified}
            )
            sellers[slug] = sel

        users = {}
        for email, first_name, last_name, phone, seller_slug in DEMO_USERS:
            user = User.objects.filter(email__iexact=email).first()
            if not user:
                user = User.objects.create(
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    phone=phone,
                    is_active=True,
                )
            else:
                user.first_name = first_name
                user.last_name = last_name
                user.phone = phone
                user.is_active = True
            user.set_password(DEMO_PASSWORD)
            user.save()
            users[email] = user
            if seller_slug:
                sellers[seller_slug].owner = user
                sellers[seller_slug].save(update_fields=["owner"])

        buyer = users["buyer@uzum.uz"]

        def create_product(spec, status):
            if len(spec) == 13:
                (
                    seller_slug,
                    title,
                    category_slug,
                    brand,
                    price,
                    old_price,
                    stock,
                    is_ad,
                    delivery,
                    characteristics,
                    days_ago,
                    images,
                    description,
                ) = spec
            else:
                (
                    seller_slug,
                    title,
                    category_slug,
                    brand,
                    price,
                    old_price,
                    stock,
                    is_ad,
                    delivery,
                    characteristics,
                    days_ago,
                ) = spec
                images = None
                description = None

            slug = unique_slug(Product, title, max_length=140)
            if not images:
                images = [f"/products/gen/{slug}-1.svg", f"/products/gen/{slug}-2.svg"]
                for i, image in enumerate(images):
                    write_svg(image.removeprefix("/products/gen/"), EMOJI_BY_CATEGORY[category_slug], brand, i)
            views = 40 + (sum(ord(c) for c in title) % 900)
            return Product.objects.create(
                seller=sellers[seller_slug],
                category=categories[category_slug],
                slug=slug,
                title=title,
                description=description
                or (
                    f"{title} — проверенный товар магазина {sellers[seller_slug].name}. "
                    f"Доставка по Ташкенту {delivery.lower()}, возврат 14 дней. "
                    "Товар прошёл проверку перед отправкой, комплектация соответствует описанию."
                ),
                price=price,
                old_price=old_price,
                stock=stock,
                brand=brand,
                delivery_time=delivery,
                is_ad=is_ad,
                status=status,
                views=views,
                images=images,
                characteristics=characteristics,
                created_at=now - timedelta(days=days_ago),
            )

        products = [create_product(spec, Product.Status.ACTIVE) for spec in PRODUCTS]
        create_product(DRAFT_PRODUCT, Product.Status.DRAFT)

        # Отзывы: ровно 2 на товар; отзыв покупателя (товар 0) привязан к аккаунту buyer.
        for product_index, rows in REVIEWS.items():
            product = products[product_index]
            for row_index, (author, rating, text, pros, cons, seller_reply, verified) in enumerate(rows):
                is_buyer_review = product_index == 0 and row_index == 0
                Review.objects.create(
                    product=product,
                    user=buyer if is_buyer_review else None,
                    author=author,
                    rating=rating,
                    text=text,
                    pros=pros,
                    cons=cons,
                    verified=verified or is_buyer_review,
                    seller_reply=seller_reply,
                    created_at=now - timedelta(days=product_index % 30, hours=row_index * 3),
                )
            recompute_product_reviews(product)

        for promo_args in PROMO_CODES:
            PromoCode.objects.update_or_create(
                code=promo_args[0],
                defaults={"percent": promo_args[1], "min_subtotal": promo_args[2], "label": promo_args[3]},
            )

        # Демо-заказы покупателя (§9 ТЗ): суммы считает тот же код, что и в API.
        for spec in DEMO_ORDERS:
            created_at = now - timedelta(days=spec["days_ago"])
            items = [(products[item["product"]], item["qty"]) for item in spec["items"]]
            subtotal = sum(int(product.price) * qty for product, qty in items)
            totals = calc_totals(subtotal, spec["delivery_method"], spec.get("promo_code"))
            order = Order.objects.create(
                number=generate_number(),
                user=buyer,
                status=spec["status"],
                subtotal=subtotal,
                discount=totals["discount"],
                promo_code=spec.get("promo_code") if totals["promo_valid"] else None,
                delivery_cost=totals["delivery_cost"],
                total=totals["total"],
                address=spec["address"],
                pickup_point="",
                delivery_method=spec["delivery_method"],
                payment_method=spec["payment_method"],
                comment=spec["comment"],
                created_at=created_at,
            )
            for product, qty in items:
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    title=product.title,
                    image=product.image,
                    price=product.price,
                    qty=qty,
                    seller=product.seller,
                )
                Product.objects.filter(pk=product.pk).update(stock=F("stock") - qty)
            for status, days_ago, note in spec["events"]:
                OrderEvent.objects.create(
                    order=order,
                    status=status,
                    at=now - timedelta(days=days_ago),
                    note=note,
                )

        for seller in sellers.values():
            recompute_seller_reviews(seller)

        counts = (
            f"Категорий: {Category.objects.count()}, магазинов: {Seller.objects.count()}, "
            f"товаров: {Product.objects.count()} (в т.ч. черновик), отзывов: {Review.objects.count()}, "
            f"заказов: {Order.objects.count()}"
        )
        self.stdout.write(counts)
        self.stdout.write(f"Демо-аккаунты (пароль {DEMO_PASSWORD}): " + ", ".join(users))
