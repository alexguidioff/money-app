"""La migrazione che disfa i bisogni-padri e costruisce i due alberi.

Il database di partenza e' quello che lascia la B3: bisogni e piaceri come
radici, con Housing e Clothes appese sotto. Qui si parte da li' perche' e' la
forma che la migrazione trova su un database vero, non una inventata per il
test.

Numeri tondi e inventati: questo repository e' pubblico e i valori veri di chi
usa l'app non ci entrano.
"""
import unittest

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.database import Base
from app.migrations import tracked_changes
from app.models import Category, Transaction

# Le categorie del database di partenza: nome, padre, e quante volte compare
# nei movimenti. Le due associazioni dichiarate - Casa sotto Bisogni, Vestiti
# sotto Piaceri - sono quelle che la B3 aveva scritto come rami.
CATEGORIE = [
    ("Needs", None, 0, 0),
    ("Wants", None, 0, 0),
    ("Housing", "Needs", 3, 2),
    ("Clothes", "Wants", 1, 0),
    ("Utilities", None, 0, 1),
    ("Groceries", None, 4, 0),
    ("Other", None, 1, 0),
    ("Others", None, 0, 2),
    ("Income", None, 0, 1),
    ("Da categorizzare", None, 0, 1),
    ("Savings", None, 0, 0),
]

# Quante volte ogni categoria compare come spesa e come entrata: serve al test
# 10, che confronta prima e dopo la migrazione.
CONTEGGI_PRIMA = {"Housing": (3, 2), "Clothes": (1, 0), "Utilities": (0, 1), "Groceries": (4, 0),
                  "Other": (1, 0), "Others": (0, 2), "Income": (0, 1), "Da categorizzare": (0, 1)}


class MigrazioneB3bTests(unittest.TestCase):
    """Un database nella forma della B3, e la migrazione che lo corregge."""

    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            # Le due colonne della B3b non ci sono ancora: e' un database
            # creato prima di questa specifica, ed e' proprio il caso in cui
            # `create_all` non fa niente. E' l'ALTER della migrazione a
            # metterle.
            conn.execute(text("ALTER TABLE categories DROP COLUMN scope"))
            conn.execute(text("ALTER TABLE categories DROP COLUMN essenziale"))
            for nome, padre, _spese, _entrate in CATEGORIE:
                conn.execute(text(
                    "INSERT INTO categories (user_id, parent_id, name, position, active) VALUES "
                    "(:u, (SELECT id FROM categories WHERE name = :p), :n, 0, true)"),
                    {"u": 1, "p": padre, "n": nome})
            id_di = {riga.name: riga.id for riga in conn.execute(
                text("SELECT id, name FROM categories")).all()}
            contatore = 0
            for nome, _padre, spese, entrate in CATEGORIE:
                for verso, quante in (("Expenses", spese), ("Income", entrate)):
                    for _ in range(quante):
                        contatore += 1
                        conn.execute(text(
                            "INSERT INTO transactions (user_id, occurred_on, effective_on, transaction_type,"
                            " category_id, amount, details, counts_in_budget, is_recurring_template) VALUES"
                            " (1, '2026-01-10', '2026-01-10', :t, :c, :a, :d, true, false)"),
                            {"t": verso, "c": id_di[nome], "a": 100 + contatore, "d": f"Movimento {contatore}"})
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def migra(self):
        tracked_changes(self.engine)
        self.session.expire_all()

    def categorie(self) -> dict[str, Category]:
        return {riga.name: riga for riga in self.session.scalars(select(Category))}

    def movimenti_per_categoria(self) -> dict[tuple[str, str], int]:
        """Quanti movimenti ha ogni categoria, divisi per verso.

        Il confronto del test 10 si fa sui nomi e non sugli id perche' e' quello
        che l'utente vede: se un movimento passasse da una categoria all'altra,
        si vedrebbe qui.
        """
        nomi = {riga.id: riga.name for riga in self.session.scalars(select(Category))}
        conteggi: dict[tuple[str, str], int] = {}
        for movimento in self.session.scalars(select(Transaction)):
            if movimento.category_id is None:
                continue
            chiave = (nomi[movimento.category_id], movimento.transaction_type)
            conteggi[chiave] = conteggi.get(chiave, 0) + 1
        return conteggi

    def test_12_ogni_categoria_ha_uno_scope(self):
        """Le colonne nascono con l'ALTER, e nessuna riga resta senza verso.

        `scope` non e' nullo per costruzione: le categorie che c'erano prima
        sono tutte di spesa tranne quelle che il passo sulle entrate rimette a
        posto, e una categoria senza verso non si potrebbe offrire in nessuna
        tendina.
        """
        self.migra()
        versi = {riga.scope for riga in self.session.scalars(select(Category))}
        self.assertTrue(versi <= {"expense", "income"}, versi)
        self.assertNotIn(None, versi)
        self.assertNotIn("", versi)

    def test_13_nessuna_categoria_entrata_ha_essenziale(self):
        """Bisogno o piacere si dicono solo delle spese.

        Il database non lo puo' esprimere con un vincolo - sarebbe un CHECK su
        due colonne e l'app ne ha gia' uno che lo dice con un codice d'errore -
        quindi la migrazione lo lascia pulito e la rotta lo rifiuta.
        """
        self.migra()
        for riga in self.session.scalars(select(Category).where(Category.scope == "income")):
            self.assertIsNone(riga.essenziale, f"{riga.name} e' un'entrata e dice {riga.essenziale}")
