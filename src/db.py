from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .models import CartLine, Order, Product


class ShopDatabase:
    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        self._migrate()

    def _migrate(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    price_kopecks INTEGER NOT NULL CHECK(price_kopecks > 0),
                    stock INTEGER NOT NULL CHECK(stock >= 0),
                    image_path TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS carts (
                    platform TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    quantity INTEGER NOT NULL CHECK(quantity > 0),
                    PRIMARY KEY(platform, customer_id, product_id)
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    address TEXT NOT NULL,
                    comment TEXT NOT NULL DEFAULT '',
                    total_kopecks INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                    product_name TEXT NOT NULL,
                    price_kopecks INTEGER NOT NULL,
                    quantity INTEGER NOT NULL
                );
                """
            )

    @staticmethod
    def _product(row: sqlite3.Row) -> Product:
        return Product(
            id=row["id"], name=row["name"], description=row["description"],
            price_kopecks=row["price_kopecks"], stock=row["stock"],
            image_path=row["image_path"], active=bool(row["active"]),
        )

    def add_product(self, name: str, description: str, price_kopecks: int, stock: int, image_path: str | None) -> int:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO products(name, description, price_kopecks, stock, image_path, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                (name, description, price_kopecks, stock, image_path, now),
            )
            return int(cursor.lastrowid)

    def list_products(self, *, active_only: bool = True) -> list[Product]:
        query = "SELECT * FROM products"
        if active_only:
            query += " WHERE active = 1 AND stock > 0"
        query += " ORDER BY id DESC"
        with self._lock:
            return [self._product(row) for row in self._connection.execute(query)]

    def get_product(self, product_id: int) -> Product | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            return self._product(row) if row else None

    def toggle_product(self, product_id: int) -> bool | None:
        with self._lock, self._connection:
            row = self._connection.execute("SELECT active FROM products WHERE id = ?", (product_id,)).fetchone()
            if row is None:
                return None
            new_value = 0 if row["active"] else 1
            self._connection.execute("UPDATE products SET active = ? WHERE id = ?", (new_value, product_id))
            return bool(new_value)

    def delete_product(self, product_id: int) -> tuple[bool, str | None]:
        with self._lock, self._connection:
            row = self._connection.execute("SELECT image_path FROM products WHERE id = ?", (product_id,)).fetchone()
            if row is None:
                return False, None
            self._connection.execute("DELETE FROM products WHERE id = ?", (product_id,))
            return True, row["image_path"]

    def add_to_cart(self, platform: str, customer_id: str, product_id: int) -> tuple[bool, str]:
        with self._lock, self._connection:
            product = self._connection.execute(
                "SELECT name, stock, active FROM products WHERE id = ?", (product_id,)
            ).fetchone()
            if product is None or not product["active"] or product["stock"] <= 0:
                return False, "Товар уже недоступен"
            current = self._connection.execute(
                "SELECT quantity FROM carts WHERE platform = ? AND customer_id = ? AND product_id = ?",
                (platform, customer_id, product_id),
            ).fetchone()
            quantity = (current["quantity"] if current else 0) + 1
            if quantity > product["stock"]:
                return False, "В наличии больше нет экземпляров"
            self._connection.execute(
                "INSERT INTO carts(platform, customer_id, product_id, quantity) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(platform, customer_id, product_id) DO UPDATE SET quantity = quantity + 1",
                (platform, customer_id, product_id),
            )
            return True, f"«{product['name']}» добавлен в корзину"

    def change_cart(self, platform: str, customer_id: str, product_id: int, delta: int) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT quantity FROM carts WHERE platform = ? AND customer_id = ? AND product_id = ?",
                (platform, customer_id, product_id),
            ).fetchone()
            if not row:
                return
            new_quantity = row["quantity"] + delta
            product = self._connection.execute("SELECT stock FROM products WHERE id = ?", (product_id,)).fetchone()
            if new_quantity <= 0:
                self._connection.execute(
                    "DELETE FROM carts WHERE platform = ? AND customer_id = ? AND product_id = ?",
                    (platform, customer_id, product_id),
                )
            elif product and new_quantity <= product["stock"]:
                self._connection.execute(
                    "UPDATE carts SET quantity = ? WHERE platform = ? AND customer_id = ? AND product_id = ?",
                    (new_quantity, platform, customer_id, product_id),
                )

    def get_cart(self, platform: str, customer_id: str) -> list[CartLine]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT p.*, c.quantity FROM carts c JOIN products p ON p.id = c.product_id "
                "WHERE c.platform = ? AND c.customer_id = ? ORDER BY p.name",
                (platform, customer_id),
            ).fetchall()
            return [CartLine(self._product(row), row["quantity"]) for row in rows]

    def create_order(self, platform: str, customer_id: str, name: str, phone: str, address: str, comment: str) -> tuple[Order, list[CartLine]]:
        with self._lock, self._connection:
            lines = self.get_cart(platform, customer_id)
            if not lines:
                raise ValueError("Корзина пуста")
            for line in lines:
                current = self._connection.execute("SELECT stock, active FROM products WHERE id = ?", (line.product.id,)).fetchone()
                if current is None or not current["active"] or current["stock"] < line.quantity:
                    raise ValueError(f"Недостаточно товара «{line.product.name}». Обновите корзину")
            total = sum(line.subtotal_kopecks for line in lines)
            created_at = datetime.now(UTC).isoformat(timespec="seconds")
            cursor = self._connection.execute(
                "INSERT INTO orders(platform, customer_id, customer_name, phone, address, comment, total_kopecks, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (platform, customer_id, name, phone, address, comment, total, created_at),
            )
            order_id = int(cursor.lastrowid)
            for line in lines:
                self._connection.execute(
                    "INSERT INTO order_items(order_id, product_id, product_name, price_kopecks, quantity) VALUES (?, ?, ?, ?, ?)",
                    (order_id, line.product.id, line.product.name, line.product.price_kopecks, line.quantity),
                )
                self._connection.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (line.quantity, line.product.id))
            self._connection.execute("DELETE FROM carts WHERE platform = ? AND customer_id = ?", (platform, customer_id))
            return Order(order_id, platform, customer_id, name, phone, address, comment, total, "new", created_at), lines

    def recent_orders(self, limit: int = 20) -> list[Order]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [Order(row["id"], row["platform"], row["customer_id"], row["customer_name"], row["phone"], row["address"], row["comment"], row["total_kopecks"], row["status"], row["created_at"]) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
