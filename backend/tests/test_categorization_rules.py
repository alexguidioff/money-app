"""Le regole che propongono la categoria di un movimento in importazione.

Si prova prima il motore da solo - quali regole combaciano e in che ordine -
poi l'innesto nell'anteprima e le rotte. Sono due cose diverse: il motore e'
logica pura, l'innesto e' il punto in cui le regole toccano un import vero.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.categorization import MAX_REGOLE, applica, carica_regole, normalizza, scartate, suggest
from app.core_routes import (RuleBulkPayload, RuleOrderPayload, RulePayload, categorization_rules,
                             create_categorization_rule, create_categorization_rules,
                             delete_categorization_rule, reorder_categorization_rules, update_categorization_rule)
from app.database import Base, reset_current_user, set_current_user
from app.main import PENDING_CATEGORY, _resolve_category, save_pdf_transactions, statement_preview
from app.models import Account, CategorizationRule, Transaction
from tests.categorie_fixture import categoria


class MotoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def _regola(self, pattern: str, nome: str = "Groceries", **campi) -> CategorizationRule:
        # La regola punta alla riga: la categoria del test si crea qui, perche'
        # un id che non esiste non e' una categoria.
        regola = CategorizationRule(pattern=pattern, category_id=categoria(self.session, nome), **campi)
        self.session.add(regola)
        self.session.commit()
        return regola

    def _applica(self, descrizione: str, tipo: str = "Expenses", importo: str = "10") -> tuple[int, str] | None:
        return applica(carica_regole(self.session), descrizione, tipo, Decimal(importo))

    def test_nessuna_regola_lascia_il_movimento_da_categorizzare(self) -> None:
        self.assertIsNone(self._applica("spesa lidl"))
        self.assertEqual(PENDING_CATEGORY, _resolve_category(None, "Expenses"))

    def test_testo_contenuto_ignora_maiuscole_e_spazi_doppi(self) -> None:
        self._regola("spesa lidl")
        self.assertEqual((categoria(self.session, "Groceries"), "spesa lidl"), self._applica("SPESA  Lidl   via roma"))

    def test_vince_la_regola_con_posizione_minore_non_la_piu_specifica(self) -> None:
        self._regola("lidl", "Other", position=5)
        self._regola("spesa lidl", "Groceries", position=1)
        self.assertEqual((categoria(self.session, "Groceries"), "spesa lidl"), self._applica("spesa lidl"))

    def test_una_regola_spenta_non_si_applica(self) -> None:
        self._regola("spesa lidl", active=False)
        self.assertEqual([], carica_regole(self.session))
        self.assertIsNone(self._applica("spesa lidl"))

    def test_una_regex_combacia_e_il_testo_non_e_una_regex(self) -> None:
        self._regola(r"^pos \d+", "Commissions", is_regex=True)
        self.assertEqual((categoria(self.session, "Commissions"), r"^pos \d+"), self._applica("POS 12345 caffe"))
        self.session.query(CategorizationRule).delete()
        self.session.commit()
        # Lo stesso pattern senza la spunta e' testo: il punto e' un punto, non
        # "un carattere qualunque".
        self._regola("a.b")
        self.assertIsNone(self._applica("axb"))
        self.assertEqual((categoria(self.session, "Groceries"), "a.b"), self._applica("a.b caffe"))

    def test_una_regex_malformata_non_fa_esplodere_l_import(self) -> None:
        # La scrittura la rifiuta: per arrivare qui va forzata nel database.
        self._regola("(senza chiusura", is_regex=True)
        regole = carica_regole(self.session)
        self.assertEqual(["(senza chiusura"], scartate(regole))
        self.assertIsNone(applica(regole, "(senza chiusura", "Expenses", Decimal("10")))

    def test_importo_assoluto_fra_minimo_e_massimo_estremi_inclusi(self) -> None:
        self._regola("affitto", "Housing", min_amount=Decimal("10"), max_amount=Decimal("50"))
        self.assertEqual((categoria(self.session, "Housing"), "affitto"), self._applica("affitto", importo="10"))
        self.assertEqual((categoria(self.session, "Housing"), "affitto"), self._applica("affitto", importo="50"))
        self.assertEqual((categoria(self.session, "Housing"), "affitto"), self._applica("affitto", importo="-30"))
        self.assertIsNone(self._applica("affitto", importo="9.99"))
        self.assertIsNone(self._applica("affitto", importo="50.01"))

    def test_il_tipo_valorizzato_non_tocca_l_altro_tipo(self) -> None:
        self._regola("stipendio", "Salary", transaction_type="Income")
        self.assertIsNone(self._applica("stipendio", tipo="Expenses"))
        self.assertEqual((categoria(self.session, "Salary"), "stipendio"), self._applica("stipendio", tipo="Income"))

    def test_un_trasferimento_non_ha_categoria_e_nessuna_regola_lo_tocca(self) -> None:
        self._regola("giroconto")
        self.assertEqual("_", _resolve_category("Groceries", "Transfers", "Groceries"))
        self.assertEqual("_", _resolve_category(None, "Investment"))

    def test_la_categoria_della_riga_vince_sulla_regola(self) -> None:
        # Una regola riempie un vuoto, non sostituisce quello che c'e' gia'.
        self.assertEqual("Other", _resolve_category("Other", "Expenses", "Groceries"))
        self.assertEqual("Groceries", _resolve_category(None, "Expenses", "Groceries"))
        self.assertEqual(PENDING_CATEGORY, _resolve_category(PENDING_CATEGORY, "Expenses", None))

    def test_la_descrizione_normalizzata_e_solo_minuscole_e_spazi(self) -> None:
        self.assertEqual("spesa lidl via roma", normalizza("  Spesa   Lidl  via roma "))


class RotteTests(unittest.TestCase):
    """Le regole scritte dall'interfaccia: cosa si salva e cosa si rifiuta."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        # La regola nomina una categoria che deve esistere: prima bastava la voce
        # nel vocabolario, adesso serve la riga.
        categoria(self.session, "Groceries")
        self.session.add(Account(source_group="bank", name="Banca", starting_balance=Decimal("0"),
                                 current_balance=Decimal("0"), is_active=True))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _crea(self, **campi) -> dict:
        return create_categorization_rule(RulePayload(**{"pattern": "spesa lidl", "category": "Groceries", **campi}),
                                          self.session)

    def _rifiuto(self, **campi) -> str:
        with self.assertRaises(HTTPException) as errore:
            self._crea(**campi)
        self.assertEqual(422, errore.exception.status_code)
        return str(errore.exception.detail)

    def test_un_pattern_vuoto_o_troppo_lungo_non_si_salva(self) -> None:
        self.assertEqual("rulePatternRequired", self._rifiuto(pattern="   "))
        self.assertEqual("rulePatternRequired", self._rifiuto(pattern="x" * 256))

    def test_una_regex_che_non_si_compila_si_rifiuta_subito(self) -> None:
        # Rifiutarla qui e' l'unico modo di non scoprirlo a import gia' iniziato.
        self.assertEqual("ruleRegexInvalid", self._rifiuto(pattern="(senza chiusura", is_regex=True))

    def test_una_categoria_fuori_dall_elenco_si_rifiuta(self) -> None:
        self.assertEqual("ruleCategoryUnknown", self._rifiuto(category="Inventata"))

    def test_un_intervallo_di_importo_rovesciato_si_rifiuta(self) -> None:
        self.assertEqual("ruleAmountRange", self._rifiuto(min_amount=50, max_amount=10))
        self.assertEqual("Groceries", self._crea(min_amount=10, max_amount=10)["category"])

    def test_oltre_duecento_regole_non_se_ne_aggiungono(self) -> None:
        spesa = categoria(self.session, "Groceries")
        self.session.add_all([CategorizationRule(pattern=f"regola {n}", category_id=spesa)
                              for n in range(MAX_REGOLE)])
        self.session.commit()
        self.assertEqual("ruleLimitReached", self._rifiuto(pattern="una di troppo"))

    def test_due_regole_uguali_non_si_salvano_nemmeno_senza_tipo(self) -> None:
        # Il tipo assente vale per spese ed entrate: due regole cosi' sono la
        # stessa regola scritta due volte. Il vincolo della tabella non le
        # vedrebbe, perche' in Postgres due NULL sono diversi fra loro.
        self._crea(transaction_type="Expenses")
        self.assertEqual("ruleDuplicate", self._rifiuto(transaction_type="Expenses"))
        prima = self._crea(pattern="senza tipo")["id"]
        self.assertEqual("ruleDuplicate", self._rifiuto(pattern="senza tipo"))
        # Il tipo diverso invece e' un'altra regola, e si salva.
        self.assertEqual("Groceries", self._crea(pattern="senza tipo", transaction_type="Income")["category"])
        # E modificare una regola non la fa scontrare con se stessa.
        update_categorization_rule(prima, RulePayload(pattern="senza tipo", category="Groceries"), self.session)

    def test_la_regola_nasce_in_coda_e_l_ordine_si_riscrive(self) -> None:
        prima, seconda = self._crea()["id"], self._crea(pattern="gtt")["id"]
        self.assertEqual([prima, seconda], [riga["id"] for riga in categorization_rules(self.session)["items"]])
        reorder_categorization_rules(RuleOrderPayload(ids=[seconda, prima]), self.session)
        self.assertEqual([(seconda, 0), (prima, 1)],
                         [(riga.id, riga.position) for riga in self.session.scalars(
                             select(CategorizationRule).order_by(CategorizationRule.position)).all()])

    def test_una_regola_si_modifica_e_si_cancella(self) -> None:
        regola = self._crea()
        aggiornata = update_categorization_rule(regola["id"], RulePayload(
            pattern="spesa lidl", category="Groceries", active=False), self.session)
        self.assertFalse(aggiornata["active"])
        delete_categorization_rule(regola["id"], self.session)
        self.assertEqual([], categorization_rules(self.session)["items"])
        with self.assertRaises(HTTPException) as errore:
            delete_categorization_rule(regola["id"], self.session)
        self.assertEqual(404, errore.exception.status_code)

    def test_la_regola_riempie_la_categoria_nell_anteprima_e_ne_dichiara_il_pattern(self) -> None:
        self._crea()
        anteprima = statement_preview([{"details": "SPESA  Lidl", "rawAmount": 20, "transactionType": "Expenses",
                                        "occurredOn": None, "accountName": None}], self.session)
        riga = anteprima["transactions"][0]
        self.assertEqual("Groceries", riga["category"])
        self.assertEqual("spesa lidl", riga["categoryRule"])
        self.assertTrue(riga["categoryAutomatic"])

    def test_la_categoria_proposta_dalla_regola_e_quella_che_si_salva(self) -> None:
        # Il salvataggio riceve le righe dell'anteprima cosi' come sono - e'
        # quello che manda l'interfaccia. Se buttasse via la categoria perche'
        # "automatica", l'anteprima l'avrebbe mostrata per niente.
        self._crea()
        anteprima = statement_preview([{"details": "spesa lidl", "rawAmount": 20, "transactionType": "Expenses",
                                        "occurredOn": "2026-08-05", "accountName": "Banca"}], self.session)
        esito = asyncio.run(save_pdf_transactions(anteprima["transactions"], self.session))
        self.assertEqual([], esito["errors"])
        self.assertEqual(categoria(self.session, "Groceries"), self.session.scalar(select(Transaction.category_id)))

    def test_una_regola_non_si_applica_ai_movimenti_gia_registrati(self) -> None:
        # Il percorso di scrittura non passa dalle regole: e' la proprieta' di
        # sicurezza della funzione, non una svista.
        self._crea()
        self.assertEqual(PENDING_CATEGORY, _resolve_category(None, "Expenses"))
        self.assertEqual("_", _resolve_category("Groceries", "Transfers"))


class ApprendimentoTests(unittest.TestCase):
    """Le regole ricavate dai movimenti gia' registrati."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _movimenti(self, descrizione: str, nome: str, quante: int, tipo: str = "Expenses") -> None:
        # "_" e' il segnaposto dei movimenti senza categoria: adesso vale NULL,
        # non una categoria chiamata trattino basso.
        categoria_id = None if nome == "_" else categoria(self.session, nome)
        self.session.add_all([Transaction(occurred_on=date(2026, 1, 1), effective_on=date(2026, 1, 1),
                                          transaction_type=tipo, category_id=categoria_id, amount=Decimal("10.00"),
                                          details=descrizione) for _ in range(quante)])
        self.session.commit()

    def test_un_gruppo_di_tre_righe_uguali_diventa_una_proposta_sicura(self) -> None:
        self._movimenti("spesa lidl", "Groceries", 3)
        proposte = suggest(self.session)["proposte"]
        self.assertEqual(1, len(proposte))
        self.assertEqual({"pattern": "spesa lidl", "category": "Groceries",
                          "categoryId": categoria(self.session, "Groceries"),
                          "transactionType": "Expenses",
                          "occorrenze": 3, "quota": 1.0, "fiducia": "sicura", "altre": []}, proposte[0])

    def test_due_righe_sole_non_fanno_una_proposta(self) -> None:
        # Sotto la soglia sono due righe capitate per caso, non un'abitudine.
        self._movimenti("spesa lidl", "Groceries", 2)
        self.assertEqual({"proposte": [], "incoerenti": []}, suggest(self.session))

    def test_una_maggioranza_non_schiacciante_si_propone_ma_incerta(self) -> None:
        self._movimenti("spesa lidl", "Groceries", 8)
        self._movimenti("spesa lidl", "Other", 2)
        proposta = suggest(self.session)["proposte"][0]
        self.assertEqual("incerta", proposta["fiducia"])
        self.assertEqual(0.8, proposta["quota"])
        self.assertEqual([{"category": "Other", "categoryId": categoria(self.session, "Other"), "count": 2}],
                         proposta["altre"])

    def test_un_gruppo_diviso_a_meta_finisce_fra_gli_incoerenti(self) -> None:
        # 5 e 5: non si propone, si mostra. E' un problema da guardare, non da
        # automatizzare: qualunque scelta sarebbe giusta a meta'.
        self._movimenti("parcheggio", "Car", 5)
        self._movimenti("parcheggio", "Leisure", 5)
        risultato = suggest(self.session)
        self.assertEqual([], risultato["proposte"])
        self.assertEqual("parcheggio", risultato["incoerenti"][0]["pattern"])
        self.assertEqual(10, risultato["incoerenti"][0]["occorrenze"])
        self.assertEqual([{"category": "Car", "categoryId": categoria(self.session, "Car"), "count": 5},
                          {"category": "Leisure", "categoryId": categoria(self.session, "Leisure"), "count": 5}],
                         risultato["incoerenti"][0]["categorie"])

    def test_una_descrizione_gia_coperta_da_una_regola_non_si_ripropone(self) -> None:
        spesa = categoria(self.session, "Groceries")
        self.session.add(CategorizationRule(pattern="spesa lidl", category_id=spesa, active=True))
        self.session.add(CategorizationRule(pattern="spesa coop", category_id=spesa, active=False))
        self.session.commit()
        self._movimenti("spesa lidl", "Groceries", 4)
        self._movimenti("spesa coop", "Groceries", 4)
        # La prima e' gia' coperta; la seconda no, perche' la regola e' spenta.
        self.assertEqual(["spesa coop"], [p["pattern"] for p in suggest(self.session)["proposte"]])

    def test_suggerire_non_scrive_niente(self) -> None:
        self._movimenti("spesa lidl", "Groceries", 5)
        prima = self.session.scalar(select(func.count(CategorizationRule.id)))
        suggest(self.session)
        self.assertEqual(prima, self.session.scalar(select(func.count(CategorizationRule.id))))
        self.assertEqual(0, prima)

    def test_maiuscole_e_spazi_doppi_fanno_lo_stesso_gruppo(self) -> None:
        self._movimenti("Spesa   Lidl", "Groceries", 2)
        self._movimenti("spesa lidl ", "Groceries", 1)
        proposte = suggest(self.session)["proposte"]
        self.assertEqual(["spesa lidl"], [p["pattern"] for p in proposte])
        self.assertEqual(3, proposte[0]["occorrenze"])

    def test_i_movimenti_senza_descrizione_o_senza_categoria_non_entrano(self) -> None:
        self._movimenti("", "Groceries", 5)
        self._movimenti("spesa lidl", PENDING_CATEGORY, 5)
        self._movimenti("spesa lidl ", "_", 5)
        self.assertEqual({"proposte": [], "incoerenti": []}, suggest(self.session))

    def test_un_gruppo_con_spese_ed_entrate_insieme_non_porta_il_tipo(self) -> None:
        self._movimenti("giroconto mario", "Groceries", 3, tipo="Expenses")
        self._movimenti("giroconto mario", "Groceries", 1, tipo="Income")
        self.assertIsNone(suggest(self.session)["proposte"][0]["transactionType"])

    def test_le_proposte_escono_dalla_piu_frequente_alla_meno(self) -> None:
        self._movimenti("spesa lidl", "Groceries", 3)
        self._movimenti("affitto", "Housing", 9)
        self.assertEqual(["affitto", "spesa lidl"], [p["pattern"] for p in suggest(self.session)["proposte"]])

    def test_il_lotto_scrive_le_regole_spuntate_in_coda_a_quelle_che_ci_sono(self) -> None:
        self.session.add(CategorizationRule(position=4, pattern="altra",
                                            category_id=categoria(self.session, "Groceries")))
        self.session.commit()
        create_categorization_rules(RuleBulkPayload(rules=[
            RulePayload(pattern="spesa lidl", category="Groceries", transaction_type="Expenses"),
            # Una proposta e' sempre testo contenuto: anche se arrivasse con
            # is_regex acceso, la regola nascerebbe come testo.
            RulePayload(pattern="affitto", category="Groceries", is_regex=True)]), self.session)
        regole = categorization_rules(self.session)["items"]
        self.assertEqual([("altra", 4), ("spesa lidl", 5), ("affitto", 6)],
                         [(riga["pattern"], riga["position"]) for riga in regole])
        self.assertEqual([False, False], [riga["isRegex"] for riga in regole[1:]])

    def test_un_lotto_che_sfora_il_tetto_non_ne_scrive_nessuna(self) -> None:
        spesa = categoria(self.session, "Groceries")
        self.session.add_all([CategorizationRule(pattern=f"regola {n}", category_id=spesa)
                              for n in range(MAX_REGOLE - 1)])
        self.session.commit()
        with self.assertRaises(HTTPException) as errore:
            create_categorization_rules(RuleBulkPayload(rules=[
                RulePayload(pattern="una", category="Groceries"),
                RulePayload(pattern="due", category="Groceries")]), self.session)
        self.assertEqual("ruleLimitReached", str(errore.exception.detail))
        self.assertEqual(MAX_REGOLE - 1, self.session.scalar(select(func.count(CategorizationRule.id))))

    def test_un_lotto_con_due_proposte_uguali_non_ne_scrive_mezza(self) -> None:
        # Meta' lotto scritto e meta' no lascerebbe l'utente senza sapere quali
        # regole sono passate.
        categoria(self.session, "Groceries")
        with self.assertRaises(HTTPException) as errore:
            create_categorization_rules(RuleBulkPayload(rules=[
                RulePayload(pattern="spesa lidl", category="Groceries"),
                RulePayload(pattern="spesa lidl", category="Groceries")]), self.session)
        self.assertEqual("ruleDuplicate", str(errore.exception.detail))
        self.assertEqual(0, self.session.scalar(select(func.count(CategorizationRule.id))))


@unittest.skipUnless(os.getenv("MONEY_TEST_POSTGRES") == "1", "requires isolated PostgreSQL schema")
class IsolamentoTests(unittest.TestCase):
    """Le regole di una persona non si vedono nell'anteprima di un'altra.

    Si prova sull'unico posto dove l'isolamento e' vero: le politiche di riga
    di PostgreSQL. Su un database senza politiche questo test passerebbe per
    costruzione, e non proverebbe niente.
    """

    def test_le_regole_di_un_altro_utente_non_compaiono_nell_anteprima(self) -> None:
        schema = "money_rules_test_" + uuid4().hex
        admin = create_engine(os.environ["DATABASE_ADMIN_URL"])
        limited = create_engine(os.environ["DATABASE_URL"], connect_args={"options": "-csearch_path=" + schema})
        owner = create_engine(os.environ["DATABASE_ADMIN_URL"], connect_args={"options": "-csearch_path=" + schema})
        ruolo = admin.dialect.identifier_preparer.quote(limited.url.username)
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            Base.metadata.create_all(owner)
            with owner.begin() as conn:
                conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {ruolo}'))
                conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO {ruolo}'))
                conn.execute(text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO {ruolo}'))
                for tabella in Base.metadata.tables.values():
                    if "user_id" not in tabella.c or tabella.name == "user_sessions":
                        continue
                    conn.execute(text(f'ALTER TABLE "{tabella.name}" ENABLE ROW LEVEL SECURITY'))
                    conn.execute(text(f"""CREATE POLICY isolate ON "{tabella.name}"
                        USING (user_id = nullif(current_setting('app.user_id', true), '')::integer)
                        WITH CHECK (user_id = nullif(current_setting('app.user_id', true), '')::integer)"""))
            token = set_current_user(101)
            try:
                with Session(limited) as session:
                    session.add(CategorizationRule(pattern="spesa lidl",
                                                   category_id=categoria(session, "Groceries")))
                    session.commit()
            finally:
                reset_current_user(token)
            token = set_current_user(202)
            try:
                with Session(limited) as session:
                    self.assertEqual([], carica_regole(session))
                    anteprima = statement_preview([{"details": "spesa lidl", "rawAmount": 10,
                                                    "transactionType": "Expenses", "occurredOn": None,
                                                    "accountName": None}], session)
                    self.assertEqual(PENDING_CATEGORY, anteprima["transactions"][0]["category"])
                    self.assertIsNone(anteprima["transactions"][0]["categoryRule"])
            finally:
                reset_current_user(token)
        finally:
            limited.dispose()
            Base.metadata.drop_all(owner)
            owner.dispose()
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}"'))
            admin.dispose()


if __name__ == "__main__":
    unittest.main()
