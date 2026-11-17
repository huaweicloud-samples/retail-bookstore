import json
import pathlib
import unittest


class FixtureTests(unittest.TestCase):
    def test_fixture_ids_and_prices(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        books = json.loads((root / "fixtures" / "books.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(books), 5)
        self.assertEqual(len({book["id"] for book in books}), len(books))
        self.assertTrue(all(float(book["price"]) > 0 for book in books))


if __name__ == "__main__":
    unittest.main()
