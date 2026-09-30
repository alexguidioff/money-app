"""La valuta del budget: la colonna nuova, il vincolo e il vecchio che sparisce.

I numeri qui sono inventati e tondi: questo repository e' pubblico e i valori
veri di chi usa l'app non ci entrano.

Il vincolo non e' provabile su SQLite - non si alterano i vincoli di una tabella
esistente - e la parte Postgres sta in `test_interchange_postgres.py`. Qui si
prova quello che succede uguale dappertutto: la colonna, il valore delle righe
che c'erano, e il fatto che rilanciare la migrazione non cambi niente.
"""
import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.database import Base
from app.migrations import tracked_changes
from app.models import BudgetPlan, Category

# La tabella come la trova la migrazione su un database vero: senza la valuta, e
# col vincolo che nominava quattro colonne. Il nome e' quello che si legge sul
# database dell'app (`budget_plans_period_budget_type_category_id_key`), non
# quello che genererebbe SQLAlchemy oggi.
BUDGET_DI_PRIMA = """
CREATE TABLE budget_plans (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    period DATE NOT NULL,
    budget_type VARCHAR(30) NOT NULL,
    category_id INTEGER,
    amount NUMERIC(16, 2),
    CONSTRAINT budget_plans_period_budget_type_category_id_key
        UNIQUE (user_id, period, budget_type, category_id)
)
"""


class BudgetDiPrimaTest(unittest.TestCase):
    """Un database di prima della valuta: la colonna non c'e'."""

    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        # La tabella rimessa nella forma di prima, come la troverebbe la
        # migrazione. Con `create_all` nascerebbe gia' con la valuta, e non ci
        # sarebbe niente da migrare.
        with self.engine.begin() as conn:
            conn.execute(text("DROP TABLE budget_plans"))
            conn.execute(text(BUDGET_DI_PRIMA))
            conn.execute(text("INSERT INTO budget_plans (user_id, period, budget_type, category_id, amount)"
                              " VALUES (1, '2026-01-01', 'Expenses', NULL, 900),"
                              " (1, '2026-02-01', 'Income', NULL, 2500)"))
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def righe(self):
        # Lette dall'ORM e non con una query grezza: cosi' il periodo torna una
        # data e l'importo un decimale, come li legge l'app, invece di quello che
        # il driver di SQLite ha voglia di restituire.
        return [(riga.period, riga.budget_type, riga.amount, riga.currency)
                for riga in self.session.scalars(select(BudgetPlan).order_by(BudgetPlan.period))]

    def test_la_colonna_arriva_e_le_righe_restano_com_erano(self):
        tracked_changes(self.engine)
        self.assertIn("currency", {colonna["name"] for colonna in inspect(self.engine).get_columns("budget_plans")})
        # Gli importi non si muovono di un centesimo: erano in euro, e quello
        # restano. Un default diverso avrebbe riscritto il passato di qualcuno.
        self.assertEqual(self.righe(), [(date(2026, 1, 1), "Expenses", Decimal("900.00"), "EUR"),
                                        (date(2026, 2, 1), "Income", Decimal("2500.00"), "EUR")])

    def test_rilanciarla_non_cambia_niente(self):
        tracked_changes(self.engine)
        prima = self.righe()
        tracked_changes(self.engine)
        self.session.expire_all()
        self.assertEqual(self.righe(), prima)

    def test_la_migrazione_regge_anche_senza_la_tabella(self):
        # Il caso dei test che creano una tabella per volta: una tabella che non
        # c'e' si salta, non fa fallire l'avvio dell'app - e non se la inventa.
        with self.engine.begin() as conn:
            conn.execute(text("DROP TABLE budget_plans"))
        tracked_changes(self.engine)
        self.assertFalse(inspect(self.engine).has_table("budget_plans"))


class BudgetFrescoTest(unittest.TestCase):
    """Un database nato col modello di oggi: la valuta sta nel vincolo.

    E' anche la prova che il nome scritto a mano in `models.py` e quello che la
    migrazione cerca sono **lo stesso nome**: se divergessero, un database nuovo
    e uno migrato avrebbero due vincoli diversi e la migrazione ne aggiungerebbe
    un terzo a ogni avvio.
    """

    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.categoria = Category(user_id=1, name="Casa", position=0, active=True)
        self.session.add(self.categoria)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_il_vincolo_porta_la_valuta_e_ha_il_nome_scritto_a_mano(self):
        vincoli = inspect(self.engine).get_unique_constraints("budget_plans")
        self.assertIn("budget_plans_period_type_category_currency_key",
                      {vincolo["name"] for vincolo in vincoli})
        colonne = next(vincolo["column_names"] for vincolo in vincoli
                       if vincolo["name"] == "budget_plans_period_type_category_currency_key")
        self.assertEqual(set(colonne), {"user_id", "period", "budget_type", "category_id", "currency"})

    def test_due_valute_nella_stessa_categoria_convivono(self):
        for valuta in ("EUR", "CHF"):
            self.session.add(BudgetPlan(user_id=1, period=date(2026, 1, 1), budget_type="Expenses",
                                        category_id=self.categoria.id, amount=Decimal("100"), currency=valuta))
        self.session.commit()
        self.assertEqual(sorted(self.session.scalars(select(BudgetPlan.currency)).all()), ["CHF", "EUR"])

    def test_la_stessa_valuta_due_volte_no(self):
        for _ in range(2):
            self.session.add(BudgetPlan(user_id=1, period=date(2026, 1, 1), budget_type="Expenses",
                                        category_id=self.categoria.id, amount=Decimal("100"), currency="EUR"))
        with self.assertRaises(Exception):
            self.session.commit()
        self.session.rollback()

    def test_chi_non_dichiara_la_valuta_scrive_euro(self):
        # Il default sta anche nel modello, non solo nella migrazione: chi crea
        # una riga senza pensarci - i test, un import vecchio - scrive euro.
        self.session.add(BudgetPlan(user_id=1, period=date(2026, 3, 1), budget_type="Expenses",
                                    category_id=self.categoria.id, amount=Decimal("50")))
        self.session.commit()
        self.assertEqual(self.session.scalar(select(BudgetPlan).where(BudgetPlan.period == date(2026, 3, 1))).currency,
                         "EUR")


if __name__ == "__main__":
    unittest.main()
