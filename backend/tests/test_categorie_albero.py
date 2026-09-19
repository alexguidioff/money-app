"""L'albero delle categorie: due livelli, nomi unici dove servono, cancellazioni
che si fermano prima di lasciare riferimenti appesi.

I nomi e gli importi sono inventati e tondi: questo repository e' pubblico e i
valori veri di chi usa l'app non ci entrano.
"""
import os
import unittest
from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.categorie import (CategoryPayload, aggiorna_categoria, cancella_categoria, crea_categoria,
                           elenco_categorie)
from app.database import Base, reset_current_user, set_current_user
from app.models import BudgetPlan, Category, Transaction, User


class CategorieTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def crea(self, nome, parent_id=None, **extra):
        return crea_categoria(CategoryPayload(name=nome, parentId=parent_id, **extra), self.session)

    def rifiuto(self, atteso, chiamata):
        with self.assertRaises(HTTPException) as errore:
            chiamata()
        self.assertEqual(errore.exception.status_code, atteso, errore.exception.detail)
        return errore.exception.detail

    def test_1_una_categoria_senza_padre_e_una_radice(self):
        creata = self.crea("Alimentari")
        self.assertIsNone(creata["parentId"])
        self.assertEqual([voce["name"] for voce in elenco_categorie(self.session)["items"]], ["Alimentari"])

    def test_2_un_figlio_di_un_figlio_e_troppo_profondo(self):
        alimentari = self.crea("Alimentari")
        supermercato = self.crea("Supermercato", alimentari["id"])
        self.assertEqual(supermercato["parentId"], alimentari["id"])
        self.assertEqual(self.rifiuto(422, lambda: self.crea("Surgelati", supermercato["id"])), "categoryTooDeep")

    def test_3_lo_stesso_nome_sotto_padri_diversi_e_ammesso(self):
        for padre in ("Alimentari", "Trasporti"):
            radice = self.crea(padre)
            self.assertEqual(self.crea("Altro", radice["id"])["parentId"], radice["id"])
        nomi = [voce["name"] for voce in elenco_categorie(self.session)["items"]]
        self.assertEqual(nomi, ["Alimentari", "Altro", "Trasporti", "Altro"])

    def test_4_lo_stesso_nome_nello_stesso_posto_e_rifiutato(self):
        alimentari = self.crea("Alimentari")
        self.crea("Supermercato", alimentari["id"])
        self.assertEqual(self.rifiuto(409, lambda: self.crea("Supermercato", alimentari["id"])), "categoryExists")
        # Anche scritto diverso: "casa" e "Casa" sarebbero due voci identiche
        # nell'elenco a tendina, e chi le legge non sa quale ha scelto.
        self.crea("Mensa", alimentari["id"])
        self.assertEqual(self.rifiuto(409, lambda: self.crea("mensa", alimentari["id"])), "categoryExists")
        self.crea("Casa")
        self.assertEqual(self.rifiuto(409, lambda: self.crea("casa")), "categoryExists")

    def test_5_un_padre_con_figli_non_si_cancella(self):
        alimentari = self.crea("Alimentari")
        self.crea("Supermercato", alimentari["id"])
        self.crea("Mensa", alimentari["id"])
        dettaglio = self.rifiuto(409, lambda: cancella_categoria(alimentari["id"], self.session))
        self.assertEqual(dettaglio, {"code": "categoryHasChildren", "children": 2})
        # Il figlio invece si cancella: sotto di lui non c'e' nessuno.
        supermercato = self.session.scalar(select(Category.id).where(Category.name == "Supermercato"))
        self.assertEqual(cancella_categoria(supermercato, self.session), {"success": True})

    def test_6_una_categoria_con_movimenti_non_si_cancella(self):
        alimentari = self.crea("Alimentari")
        for importo in ("10.00", "20.00", "30.00"):
            self.session.add(Transaction(occurred_on=date(2026, 4, 1), effective_on=date(2026, 4, 1),
                                         transaction_type="Expenses", category_id=alimentari["id"],
                                         amount=Decimal(importo), account_name="Conto"))
        self.session.add(BudgetPlan(period=date(2026, 4, 1), budget_type="Expenses",
                                    category_id=alimentari["id"], amount=Decimal("500.00")))
        self.session.commit()
        dettaglio = self.rifiuto(409, lambda: cancella_categoria(alimentari["id"], self.session))
        self.assertEqual(dettaglio, {"code": "categoryInUse", "movements": 3, "budgets": 1, "rules": 0})
        # Spenta resta: e' il modo di mettere via una categoria senza
        # riscrivere la storia di chi l'ha usata.
        aggiorna_categoria(alimentari["id"], CategoryPayload(active=False), self.session)
        self.assertFalse(elenco_categorie(self.session)["items"][0]["active"])
        for riga in self.session.query(Transaction).all():
            riga.category_id = None
        self.session.query(BudgetPlan).delete()
        self.session.commit()
        self.assertEqual(cancella_categoria(alimentari["id"], self.session), {"success": True})
        self.assertEqual(elenco_categorie(self.session)["items"], [])

    def test_lo_spostamento_non_crea_nipoti_e_il_nome_si_cambia(self):
        alimentari, trasporti = self.crea("Alimentari"), self.crea("Trasporti")
        self.crea("Supermercato", alimentari["id"])
        # "Alimentari" ha figli: spostarlo sotto un'altra radice farebbe un
        # terzo livello, e l'albero si accartoccia di nascosto.
        self.assertEqual(self.rifiuto(422, lambda: aggiorna_categoria(
            alimentari["id"], CategoryPayload(parentId=trasporti["id"]), self.session)), "categoryTooDeep")
        aggiornata = aggiorna_categoria(alimentari["id"], CategoryPayload(name="Spesa"), self.session)
        self.assertEqual((aggiornata["name"], aggiornata["parentId"]), ("Spesa", None))
        self.assertEqual(self.session.get(Category, alimentari["id"]).position, 1)


class SchemaIsolato:
    """Uno schema PostgreSQL usa-e-getta con le politiche di riga vere.

    Le tabelle le crea qui dentro: quelle dell'app non vengono lette ne'
    toccate, e alla fine lo schema sparisce. Senza politiche l'isolamento non
    si puo' provare, e con le politiche di produzione non si puo' provare
    niente senza rischiare i dati veri.
    """

    def __enter__(self):
        self.schema = "money_cat_test_" + uuid4().hex
        self.admin = create_engine(os.environ["DATABASE_ADMIN_URL"])
        self.limited = create_engine(os.environ["DATABASE_URL"],
                                     connect_args={"options": "-csearch_path=" + self.schema})
        self.owner = create_engine(os.environ["DATABASE_ADMIN_URL"],
                                   connect_args={"options": "-csearch_path=" + self.schema})
        ruolo = self.admin.dialect.identifier_preparer.quote(self.limited.url.username)
        with self.admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        Base.metadata.create_all(self.owner)
        with self.owner.begin() as conn:
            conn.execute(text(f'GRANT USAGE ON SCHEMA "{self.schema}" TO {ruolo}'))
            conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{self.schema}" TO {ruolo}'))
            conn.execute(text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{self.schema}" TO {ruolo}'))
            for tabella in Base.metadata.tables.values():
                if "user_id" not in tabella.c or tabella.name == "user_sessions":
                    continue
                conn.execute(text(f'ALTER TABLE "{tabella.name}" ENABLE ROW LEVEL SECURITY'))
                conn.execute(text(f"""CREATE POLICY isolate ON "{tabella.name}"
                    USING (user_id = nullif(current_setting('app.user_id', true), '')::integer)
                    WITH CHECK (user_id = nullif(current_setting('app.user_id', true), '')::integer)"""))
        with Session(self.owner) as session:
            session.add_all([User(id=101, username="uno", display_name="Uno"),
                             User(id=202, username="due", display_name="Due")])
            session.commit()
        return self

    def __exit__(self, *errore):
        with self.admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        for engine in (self.limited, self.owner, self.admin):
            engine.dispose()
        return False


@unittest.skipUnless(os.getenv("MONEY_TEST_POSTGRES") == "1", "requires isolated PostgreSQL schema")
class IsolamentoCategorieTests(unittest.TestCase):
    def test_9_le_categorie_di_uno_non_si_vedono_dall_altro(self):
        with SchemaIsolato() as schema:
            token = set_current_user(101)
            try:
                with Session(schema.limited) as session:
                    mia = crea_categoria(CategoryPayload(name="Casa"), session)
                    crea_categoria(CategoryPayload(name="Supermercato", parentId=mia["id"]), session)
                    self.assertEqual(len(elenco_categorie(session)["items"]), 2)
            finally:
                reset_current_user(token)
            token = set_current_user(202)
            try:
                with Session(schema.limited) as session:
                    self.assertEqual(elenco_categorie(session)["items"], [])
                    with self.assertRaises(HTTPException) as errore:
                        cancella_categoria(mia["id"], session)
                    self.assertEqual(errore.exception.status_code, 404)
            finally:
                reset_current_user(token)


if __name__ == "__main__":
    unittest.main()
