import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from position_store import initialize, add_position, get_positions, delete_position


class PositionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "positions.sqlite3"
        initialize(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_users_are_isolated(self):
        row_id = add_position(self.db, 1001, "NVDA", Decimal("181.25"), Decimal("1.5"), "DEMO-001")
        self.assertEqual(len(get_positions(self.db, 1001)), 1)
        self.assertEqual(get_positions(self.db, 1002), [])
        self.assertFalse(delete_position(self.db, 1002, row_id))
        self.assertEqual(len(get_positions(self.db, 1001)), 1)
        self.assertTrue(delete_position(self.db, 1001, row_id))
        self.assertEqual(get_positions(self.db, 1001), [])

    def test_invalid_entries_rejected(self):
        with self.assertRaises(ValueError):
            add_position(self.db, 1001, "NVDA", Decimal("-2"), Decimal("1"), "")
        with self.assertRaises(ValueError):
            add_position(self.db, 1001, "NVDA", Decimal("2"), Decimal("0"), "")


if __name__ == "__main__":
    unittest.main()
