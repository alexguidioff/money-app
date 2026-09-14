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

    def test_date_con_il_mese_in_lettere(self):
        # Alcune banche scrivono "01 lug 2026", altre il mese per esteso.
        self.assertEqual(parse_date('01 lug 2026'), '2026-07-01')
        self.assertEqual(parse_date('3 August 2026'), '2026-08-03')
        self.assertEqual(parse_date('5 juin 2026'), '2026-06-05')
        self.assertEqual(parse_date('5 juil. 2026'), '2026-07-05')
        self.assertIsNone(parse_date('31 feb 2026'))
        self.assertIsNone(parse_date('1 xyz 2026'))


if __name__ == '__main__':
    unittest.main()
