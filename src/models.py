from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class Product:
    id: int
    name: str
    description: str
    price_kopecks: int
    stock: int
    image_path: str | None
    active: bool


@dataclass(frozen=True, slots=True)
class CartLine:
    product: Product
    quantity: int

    @property
    def subtotal_kopecks(self) -> int:
        return self.product.price_kopecks * self.quantity


@dataclass(frozen=True, slots=True)
class Order:
    id: int
    platform: str
    customer_id: str
    customer_name: str
    phone: str
    address: str
    comment: str
    total_kopecks: int
    status: str
    created_at: str


def parse_price(value: str) -> int:
    normalized = value.strip().replace(" ", "").replace(",", ".")
    try:
        price = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Цена должна быть числом, например 1250 или 1250,50") from exc
    if price <= 0:
        raise ValueError("Цена должна быть больше нуля")
    kopecks = int((price * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if kopecks > 100_000_000_00:
        raise ValueError("Слишком большая цена")
    return kopecks


def format_money(kopecks: int, currency: str = "₽") -> str:
    rubles, coins = divmod(kopecks, 100)
    spaced = f"{rubles:,}".replace(",", " ")
    return f"{spaced}{currency}" if coins == 0 else f"{spaced},{coins:02d}{currency}"
