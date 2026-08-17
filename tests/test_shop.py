from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.db import ShopDatabase
from src.max_api import MaxAPI
from src.max_shop import MaxShopBot
from src.models import format_money, parse_price


class PriceTests(unittest.TestCase):
    def test_parse_and_format_price(self) -> None:
        self.assertEqual(parse_price("1 250,50"), 125_050)
        self.assertEqual(format_money(125_050), "1 250,50₽")

    def test_rejects_invalid_price(self) -> None:
        for value in ("", "abc", "0", "-5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_price(value)


class MaxPayloadTests(unittest.TestCase):
    def test_callback_user_and_keyboard_payload(self) -> None:
        update = {"callback": {"user": {"user_id": 77}}}
        self.assertEqual(MaxShopBot._user_id(update), 77)
        keyboard = MaxAPI.keyboard([[('Купить', 'add:1')]])
        button = keyboard["payload"]["buttons"][0][0]
        self.assertEqual(button, {"type": "callback", "text": "Купить", "payload": "add:1"})


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = ShopDatabase(Path(self.temp_dir.name) / "shop.db")

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_product_cart_and_order_flow(self) -> None:
        product_id = self.db.add_product("Свеча", "Соевый воск", 75_000, 2, None)
        ok, _ = self.db.add_to_cart("telegram", "42", product_id)
        self.assertTrue(ok)
        order, lines = self.db.create_order("telegram", "42", "Анна", "+79001234567", "Москва", "")
        self.assertEqual(order.total_kopecks, 75_000)
        self.assertEqual(lines[0].quantity, 1)
        self.assertEqual(self.db.get_product(product_id).stock, 1)  # type: ignore[union-attr]
        self.assertEqual(self.db.get_cart("telegram", "42"), [])

    def test_cannot_add_more_than_stock(self) -> None:
        product_id = self.db.add_product("Брошь", "Дерево", 10_000, 1, None)
        self.assertTrue(self.db.add_to_cart("max", "7", product_id)[0])
        self.assertFalse(self.db.add_to_cart("max", "7", product_id)[0])

    def test_order_history_survives_product_deletion(self) -> None:
        product_id = self.db.add_product("Кружка", "Керамика", 50_000, 1, None)
        self.db.add_to_cart("telegram", "42", product_id)
        order, _ = self.db.create_order("telegram", "42", "Иван", "+79001234567", "Казань", "")
        deleted, image_path = self.db.delete_product(product_id)
        self.assertTrue(deleted)
        self.assertIsNone(image_path)
        self.assertEqual(self.db.recent_orders()[0].id, order.id)


if __name__ == "__main__":
    unittest.main()
