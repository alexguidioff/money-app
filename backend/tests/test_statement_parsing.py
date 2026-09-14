import unittest

from app.statement_parsing import parse_amount, parse_date


class ImportiEstrattoConto(unittest.TestCase):
    def test_separatori(self):
        casi = {
            '45,30': 45.30, '-45,30': -45.30, '1.500,00': 1500.00,
            '1,500.00': 1500.00, '1.234.567,89': 1234567.89,
            '€ 1.500,00': 1500.00, '1500': 1500.0, '1.500': 1500.0,
            '100-': -100.0, '(100,00)': -100.0, '': 0.0, 'abc': 0.0,
        }
        for testo, atteso in casi.items():
            self.assertAlmostEqual(parse_amount(testo), atteso, places=2, msg=testo)

    def test_date(self):
        self.assertEqual(parse_date('05/09/2026'), '2026-09-05')
        self.assertEqual(parse_date('2026-09-05'), '2026-09-05')
        self.assertIsNone(parse_date('non una data'))


if __name__ == '__main__':
    unittest.main()
