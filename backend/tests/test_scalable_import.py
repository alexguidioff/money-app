"""L'elenco di Scalable letto e importato, su un elenco finto.

Il testo vero di chi lo importa non entra qui: il repository e' pubblico, e quei
movimenti sono suoi. L'elenco lo costruisce il test, con nomi di strumenti
inventati e cifre tonde, e usa **i separatori veri** - fra le colonne c'e' un
separatore di riga Unicode, non uno spazio: e' l'unica differenza fra
l'incollare e l'esportare, e un testo finto separato da spazi non proverebbe
quella lettura.

Quello che si fissa qui sono le cose che l'elenco non dice e l'import deduce: il
prezzo si ricava dall'importo diviso le quote, i versamenti si agganciano alle
righe di banca esistenti (una riga per un versamento, mai due), le grafie dello
stesso fondo diventano un nome solo, e le righe interne della piattaforma si
scartano tutte e due invece di importarne una.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import main
from app.calculation_engine import calculate_account_balance
from app.database import Base, reset_current_user, set_current_user
from app.market_data import MarketDataError, SymbolMatch
from app.models import (Account, AppSetting, ImportBatch, InvestmentInstrument,
                        InvestmentTransaction, Transaction, TransactionLedgerLink, User)
from app.scalable_import import leggi_elenco

UTENTE = 7


def colonne(*celle: str) -> str:
    """Una riga dell'elenco: fra le colonne c'e' il separatore vero, U+2028.

    E' quello che l'interfaccia mette fra il tipo, il nome, le quote e
    l'importo, ed e' il motivo per cui una riga letta con `splitlines()` si
    spezza in quattro. Scritto qui una volta per tutti i test.
    """
    return " ".join(celle)


# L'elenco finto: date, versamenti, piani di accumulo, un interesse, una vendita,
# e le righe che devono essere rifiutate (la migrazione interna dei titoli, la
# coppia di versamenti che si annulla, la riga scaduta senza importo, un tipo
# che non si conosce, un titolo senza nome).
#
# Gli importi portano il segno **come li scrive la piattaforma**: quello che esce
# e' negativo, compreso il versamento negativo del primo agosto, che e' il
# ritorno di un versamento - una riga che si chiama "Deposito" e porta denaro
# fuori.
RIGHE = [
    "lunedì, 3 febbraio 2025", colonne("Deposito", "€1.000,00"),
    "martedì, 4 febbraio 2025", colonne("Piano di accumulo", "Fondo Alfa UCITS ETF 1C", "10,00 az.", "€-200,00"),
    "mercoledì, 5 febbraio 2025", colonne("Piano di accumulo", "Fondo Beta ETC", "4,00 az.", "€-80,00"),
    "lunedì, 10 febbraio 2025", colonne("Piano di accumulo", "Fondo Alfa UCITS ETF Acc", "5,00 az.", "€-120,00"),
    "mercoledì, 5 marzo 2025", colonne("Deposito", "€500,00"),
    "martedì, 1 aprile 2025", colonne("Deposito", "€300,00"),
    "martedì, 15 aprile 2025", colonne("Deposito", "€300,00"),
    "giovedì, 1 maggio 2025", colonne("Interesse", "€12,34"),
    "lunedì, 2 giugno 2025", colonne("Vendita", "Fondo Alfa UCITS ETF Acc", "5,00 az.", "€150,00"),
    "mercoledì, 25 giugno 2025", colonne("Prelievo", "€-40,00"),
    "giovedì, 10 luglio 2025", colonne("Deposito", "€7,50"),
    "venerdì, 11 luglio 2025", colonne("Prelievo", "€-7,50"),
    "venerdì, 1 agosto 2025", colonne("Deposito Return: Scalable Capital Broker", "€-100,00"),
    "lunedì, 1 settembre 2025", colonne("Acquisto", "Fondo Gamma", "3,00 az.", "Scaduto"),
    "martedì, 2 settembre 2025", colonne("Commissione", "€1,00"),
    "mercoledì, 3 settembre 2025", colonne("Acquisto", "3,00 az.", "€30,00"),
    "venerdì, 5 dicembre 2025", colonne("Trasferisci", "Fondo Beta Inc -B", "7,00 az.", "€-700,00"),
    "sabato, 6 dicembre 2025", colonne("Trasferimento in entrata", "Fondo Beta Inc -B", "7,00 az.", "€710,00"),
]
TESTO = "\n".join(RIGHE)

# Le righe di banca del conto: i tre versamenti che trovano la loro riga entro i
# cinque giorni, quello da 500 che ce l'ha a nove (si sceglie a mano), e due
# esche che non devono essere prese - un'entrata dello stesso importo (il verso
# sbagliato) e un giroconto dello stesso giorno.
BANCA = [
    ("2025-02-04", "Expenses", "1.000,00"),
    ("2025-03-14", "Expenses", "500,00"),
    ("2025-04-02", "Expenses", "300,00"),
    ("2025-04-16", "Expenses", "300,00"),
    ("2025-06-25", "Income", "40,00"),
    ("2025-04-02", "Income", "300,00"),
    ("2025-02-04", "Transfers", "1.000,00"),
]


class Lettura(unittest.TestCase):
    """Il testo letto: righe, rifiuti con motivo, strumenti raggruppati."""

    def test_le_righe_di_una_giornata_stanno_sotto_la_loro_data(self) -> None:
        letto = leggi_elenco(TESTO)
        primo = letto["movimenti"][0]
        self.assertEqual(primo["giorno"], date(2025, 2, 3))
        self.assertEqual(primo["tipo"], "Deposito")
        self.assertEqual(primo["importo"], Decimal("1000.00"))
        self.assertIsNone(primo["quote"])

    def test_un_titolo_porta_le_quote_e_l_importo(self) -> None:
        letto = leggi_elenco(TESTO)
        acquisto = next(r for r in letto["movimenti"] if r["tipo"] == "Piano di accumulo")
        self.assertEqual(acquisto["nome"], "Fondo Alfa UCITS ETF 1C")
        self.assertEqual(acquisto["quote"], Decimal("10.00"))
        # L'importo con il suo segno: comprando il denaro esce, e il segno e' la
        # sola cosa che lo dice (le quote sono sempre scritte positive).
        self.assertEqual(acquisto["importo"], Decimal("-200.00"))

    def test_le_colonne_separate_da_spazi_si_leggono_uguale(self) -> None:
        """Il separatore vero e' invisibile; chi lo perde scrivendo a mano non deve
        ritrovarsi un elenco illeggibile."""
        con_spazi = "lunedì, 3 febbraio 2025\nDeposito €1.000,00\n"
        letto = leggi_elenco(con_spazi)
        self.assertEqual(len(letto["movimenti"]), 1)
        self.assertEqual(letto["movimenti"][0]["importo"], Decimal("1000.00"))

    def test_una_riga_di_movimento_senza_data_non_si_importa(self) -> None:
        letto = leggi_elenco(colonne("Deposito", "€100,00") + "\n")
        self.assertEqual(letto["movimenti"], [])
        self.assertEqual([r["motivo"] for r in letto["rifiuti"]], ["rigaSenzaData"])

    def test_ogni_riga_scartata_ha_il_suo_motivo(self) -> None:
        motivi = {r["riga"]: r["motivo"] for r in leggi_elenco(TESTO)["rifiuti"]}
        self.assertEqual(sorted(motivi.values()),
                         ["coppiaInterna", "coppiaInterna", "rigaSconosciuta",
                          "rigaSenzaImporto", "strumentoSenzaNome",
                          "versamentiAnnullati", "versamentiAnnullati"])

    def test_le_due_gambe_della_migrazione_si_scartano_tutte_e_due(self) -> None:
        """Una gamba sola muoverebbe il portafoglio di quote mai passate di li'."""
        letto = leggi_elenco(TESTO)
        nomi = {r["nome"] for r in letto["movimenti"]}
        self.assertNotIn("Fondo Beta Inc -B", nomi)

    def test_due_grafie_dello_stesso_fondo_diventano_uno_strumento(self) -> None:
        letto = leggi_elenco(TESTO)
        self.assertEqual(len(letto["strumenti"]), 2)
        alfa = next(s for s in letto["strumenti"] if "Alfa" in s["nome"])
        # Il nome canonico e' quello dell'ultima riga: e' come lo strumento si
        # chiama adesso, ed e' quello che si confronta con la piattaforma.
        self.assertEqual(alfa["nome"], "Fondo Alfa UCITS ETF Acc")
        self.assertEqual(len(alfa["grafie"]), 2)
        # Comprare 10, comprare 5 e venderne 5: restano 10 quote.
        self.assertEqual(alfa["quote"], Decimal("10.00000000"))
        self.assertEqual(alfa["righe"], 3)

    def test_due_strumenti_diversi_restano_due(self) -> None:
        letto = leggi_elenco(TESTO)
        self.assertEqual(sorted(s["nome"] for s in letto["strumenti"]),
                         ["Fondo Alfa UCITS ETF Acc", "Fondo Beta ETC"])


class ConUnElenco(unittest.TestCase):
    """L'anteprima e l'import, su un database vuoto con le righe di banca."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        token = set_current_user(UTENTE)
        self.addCleanup(reset_current_user, token)
        self.session.add(User(id=UTENTE, username="prova", display_name="Prova"))
        self.session.add(Account(id=1, source_group="bank", name="Conto", currency="EUR"))
        # Un'entrata tardiva slitta al mese dopo: la riga di banca del prelievo
        # del 25 giugno ha la competenza al primo luglio, e l'import la riporta
        # al giorno in cui i soldi si sono mossi davvero.
        self.session.add(AppSetting(user_id=UTENTE, key="late_income_shift", label="Turni", value="Active"))
        self.session.add(AppSetting(user_id=UTENTE, key="late_income_day", label="Giorno", value="20"))
        for giorno, tipo, importo in BANCA:
            quando = date.fromisoformat(giorno)
            self.session.add(Transaction(
                occurred_on=quando,
                # Un'entrata incassata dal giorno 20 in poi pesa sul mese dopo: e'
                # quello che l'app scrive, ed e' la competenza che l'import
                # riporta al giorno vero quando la riga diventa un giroconto.
                effective_on=(date(quando.year, quando.month + 1, 1)
                              if tipo == "Income" and quando.day >= 20 else quando),
                transaction_type=tipo, amount=Decimal(importo.replace(".", "").replace(",", ".")),
                account_name="Conto", currency="EUR",
                destination_name="Conto2" if tipo == "Transfers" else None))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    # -- utilita' ------------------------------------------------------------

    def _piano(self, **extra):
        return main._piano_scalable(self.session, TESTO, **extra)

    def _preview(self, testo: str = TESTO) -> dict:
        """L'anteprima con la ricerca dei ticker spenta: e' un servizio esterno, e
        un test non lo interroga. Gli strumenti restano senza proposte."""
        return self._preview_con(testo, side_effect=MarketDataError("fonte spenta"))

    def _preview_con(self, testo: str = TESTO, **ricerca) -> dict:
        with mock.patch.object(main, "search_yahoo_symbols", **ricerca):
            return main.scalable_preview(main.ScalablePreviewPayload(testo=testo), self.session)

    def _senza_riga(self) -> dict:
        """Il primo versamento che non ha una riga di banca: l'unico che l'utente
        puo' agganciare a mano."""
        return next(r for r in self._piano()["movimenti"]
                    if r["tipo"] in main.RIGHE_CONTANTI and r["banca"] is None)

    def _importa(self, **extra):
        payload = main.ScalableImportPayload(testo=TESTO, utente=UTENTE)
        for chiave, valore in extra.items():
            setattr(payload, chiave, valore)
        return main.import_scalable(payload, self.session)

    def _conti(self) -> dict[str, Account]:
        return {conto.name: conto for conto in self.session.scalars(select(Account)).all()}

    # -- anteprima -----------------------------------------------------------

    def test_l_anteprima_conta_quello_che_si_importa(self) -> None:
        anteprima = self._preview()
        self.assertEqual(anteprima["counts"]["importable"], 11)
        self.assertEqual(anteprima["counts"]["rejected"], 7)
        # Le righe di data non sono movimenti: delle diciassette righe di data e
        # diciotto di movimento ne restano undici da importare e sette rifiutate.
        self.assertEqual(anteprima["counts"]["read"], 18)
        self.assertEqual(anteprima["user"], {"id": UTENTE, "username": "prova", "displayName": "Prova"})

    def test_l_anteprima_aggancia_i_versamenti_e_mostra_quelli_senza_riga(self) -> None:
        versamenti = self._preview()["deposits"]
        self.assertEqual(versamenti["totali"], 6)
        self.assertEqual(versamenti["conRiga"], 4)
        self.assertEqual(versamenti["daFuori"], 2)
        # Il versamento da 500 senza riga vicina e la restituzione da 100: il
        # secondo entra nei versamenti col segno che ha, cioe' negativo.
        self.assertEqual(versamenti["importoFuori"], 400.0)
        senza = self._senza_riga()
        self.assertEqual(senza["importo"], Decimal("500.00"))
        # La riga a nove giorni non e' un aggancio automatico ma e' fra le
        # candidate, e la distanza si dice: chi importa la sceglie sapendolo.
        riga = next(v for v in self._preview()["bankLinks"] if v["row"] == senza["riga"])
        self.assertFalse(riga["matched"])
        self.assertEqual([c["gapDays"] for c in riga["candidates"]], [9])

    def test_una_riga_di_banca_vale_per_un_versamento_solo(self) -> None:
        """Due versamenti uguali e una riga sola: uno si aggancia, l'altro no.

        E' il caso che rompe l'aggancio ingenuo, che darebbe la stessa uscita a
        tutti e due e conterebbe due volte lo stesso denaro.
        """
        testo = "\n".join([
            "lunedì, 3 febbraio 2025", colonne("Deposito", "€1.000,00"),
            "mercoledì, 5 febbraio 2025", colonne("Deposito", "€1.000,00"),
        ])
        piano = main._piano_scalable(self.session, testo)
        self.assertEqual(sum(1 for r in piano["movimenti"] if r["banca"] is not None), 1)
        # E i due versamenti non si scambiano il partner: quello senza riga non
        # si aggancia alla riga che l'altro ha gia' preso.
        self.assertEqual([r["banca"] is not None for r in piano["movimenti"]], [True, False])

    def test_il_verso_sbagliato_non_si_aggancia(self) -> None:
        """Un'entrata dello stesso importo non e' il pagamento di un versamento."""
        testo = "lunedì, 1 aprile 2025\n" + colonne("Deposito", "€300,00") + "\n"
        piano = main._piano_scalable(self.session, testo)
        righe = {r["banca"].transaction_type for r in piano["movimenti"] if r["banca"] is not None}
        self.assertEqual(righe, {"Expenses"})

    def test_il_segno_decide_da_che_parte_va_il_denaro(self) -> None:
        """Una riga che si chiama "Deposito" e porta un importo negativo esce.

        Il tipo dice come la piattaforma chiama il movimento, non da che parte
        vanno i soldi: e' il segno a dirlo. Una restituzione di versamento cerca
        un'entrata in banca, non una spesa, e il conto da cui parte e' quello del
        broker invece della banca.
        """
        testo = "mercoledì, 25 giugno 2025\n" + colonne("Deposito Return", "€-40,00") + "\n"
        piano = main._piano_scalable(self.session, testo)
        movimento = piano["movimenti"][0]
        self.assertEqual(movimento["banca"].transaction_type, "Income")
        self._importa(testo=testo)
        riga = self.session.scalar(select(Transaction).where(Transaction.occurred_on == date(2025, 6, 25),
                                                             Transaction.transaction_type == "Transfers"))
        self.assertEqual((riga.account_name, riga.destination_name), ("Scalable", "Conto"))

    def test_il_prezzo_si_ricava_e_si_vede(self) -> None:
        piano = self._piano()
        acquisto = next(r for r in piano["movimenti"] if r["tipo"] == "Piano di accumulo")
        self.assertEqual(acquisto["prezzo"], Decimal("20.00000000"))

    def test_l_anteprima_propone_i_ticker_cercati(self) -> None:
        anteprima = self._preview_con(return_value=[
            SymbolMatch(symbol="AAA", name="Fondo Alfa", exchange="Milano", quote_type="ETF")])
        alfa = next(s for s in anteprima["instruments"] if "Alfa" in s["nome"])
        self.assertEqual(alfa["candidati"][0]["symbol"], "AAA")

    def test_l_anteprima_dice_che_i_conti_non_esistono_ancora(self) -> None:
        conti = {c["nome"]: c for c in self._preview()["accounts"]}
        self.assertEqual(sorted(conti), ["Scalable", "Scalable investimenti", "Versamenti non tracciati"])
        self.assertFalse(any(c["esiste"] for c in conti.values()))
        self.assertTrue(conti["Scalable investimenti"]["broker"])
        # Il saldo che il conto avra': contante, titoli, e il negativo da fuori.
        self.assertEqual(conti["Scalable"]["saldo"], 1722.34)
        self.assertEqual(conti["Scalable investimenti"]["saldo"], 250.0)
        self.assertEqual(conti["Versamenti non tracciati"]["saldo"], -400.0)

    def test_il_contante_chiude_come_chiude_il_testo(self) -> None:
        """Depositi piu' interessi piu' vendite meno acquisti meno prelievi."""
        self.assertEqual(self._preview()["totals"]["contante"], 1722.34)
        # Il residuo e' la somma degli importi come sono scritti, segno compreso:
        # la restituzione del primo agosto toglie 100 invece di aggiungerli.
        self.assertEqual(self._preview()["totals"]["nonTracciato"], -400.0)

    # -- scrittura -----------------------------------------------------------

    def test_i_tre_conti_nascono_col_saldo_finale(self) -> None:
        self._importa()
        conti = self._conti()
        self.assertEqual(conti["Scalable"].current_balance, Decimal("1722.34"))
        self.assertTrue(conti["Scalable investimenti"].is_broker)
        self.assertEqual(conti["Scalable investimenti"].current_balance, Decimal("250.00"))
        self.assertEqual(conti["Versamenti non tracciati"].current_balance, Decimal("-400.00"))
        self.assertTrue(conti["Versamenti non tracciati"].counts_in_net_worth)
        self.assertFalse(conti["Scalable"].is_liquid)

    def test_lo_strumento_nasce_una_volta_sola_e_col_nome_canonico(self) -> None:
        self._importa(ticker={"Fondo Alfa UCITS ETF Acc": "AAA"})
        strumenti = self.session.scalars(select(InvestmentInstrument)).all()
        self.assertEqual(sorted(s.name for s in strumenti),
                         ["Fondo Alfa UCITS ETF Acc", "Fondo Beta ETC"])
        alfa = next(s for s in strumenti if "Alfa" in s.name)
        self.assertEqual(alfa.provider_symbol, "AAA")

    def test_ogni_acquisto_ha_la_sua_operazione_e_il_suo_movimento(self) -> None:
        self._importa()
        operazioni = self.session.scalars(select(InvestmentTransaction)).all()
        self.assertEqual(len(operazioni), 4)
        self.assertEqual(sum(1 for o in operazioni if o.transaction_type == "Buy"), 3)
        vendita = next(o for o in operazioni if o.transaction_type == "Sell")
        self.assertEqual(vendita.units, Decimal("5.00"))
        self.assertEqual(vendita.price, Decimal("30.00"))
        legami = self.session.scalars(select(TransactionLedgerLink)).all()
        self.assertEqual(len(legami), 4)
        movimenti = self.session.scalars(select(Transaction).where(
            Transaction.transaction_type == "Investment")).all()
        self.assertEqual(len(movimenti), 4)
        # Il movimento e' la gamba in contanti: comprando esce dal contante ed
        # entra nei titoli, vendendo il contrario.
        acquisto = next(m for m in movimenti if m.amount == Decimal("200.00"))
        self.assertEqual((acquisto.account_name, acquisto.destination_name),
                         ("Scalable", "Scalable investimenti"))

    def test_la_vendita_fa_uscire_i_soldi_dai_titoli(self) -> None:
        self._importa()
        vendita = self.session.scalar(select(Transaction).where(
            Transaction.transaction_type == "Investment",
            Transaction.account_name == "Scalable investimenti"))
        self.assertEqual(vendita.destination_name, "Scalable")
        self.assertEqual(vendita.amount, Decimal("150.00"))

    def test_l_interesse_e_un_entrata_col_suo_nome(self) -> None:
        self._importa(categoria_interessi="Interessi e dividendi")
        # Sul conto del broker, non su un conto qualunque: l'entrata da 300 del
        # conto di banca e' un'esca dei test e non deve essere toccata.
        entrate = self.session.scalars(select(Transaction).where(
            Transaction.transaction_type == "Income",
            Transaction.account_name == "Scalable")).all()
        self.assertEqual(len(entrate), 1)
        self.assertEqual(entrate[0].amount, Decimal("12.34"))
        self.assertIsNotNone(entrate[0].category_id)

    def test_la_riga_di_banca_diventa_un_giroconto_verso_il_broker(self) -> None:
        self._importa()
        riga = self.session.scalar(select(Transaction).where(
            Transaction.occurred_on == date(2025, 2, 4),
            Transaction.transaction_type == "Transfers",
            Transaction.account_name == "Conto"))
        self.assertEqual(riga.destination_name, "Scalable")
        self.assertEqual(riga.amount, Decimal("1000.00"))
        # Un giroconto non ha categoria e non pesa sul budget: quel denaro non
        # era una spesa, ed e' il motivo per cui la riga si converte.
        self.assertIsNone(riga.category_id)
        self.assertFalse(riga.counts_in_budget)

    def test_una_competenza_spostata_torna_al_giorno_vero(self) -> None:
        prima = self.session.scalar(select(Transaction).where(
            Transaction.occurred_on == date(2025, 6, 25)))
        self.assertEqual(prima.effective_on, date(2025, 7, 1))
        self._importa()
        self.session.refresh(prima)
        self.assertEqual((prima.transaction_type, prima.effective_on), ("Transfers", date(2025, 6, 25)))

    def test_le_esche_non_si_toccano(self) -> None:
        """Un'entrata di pari importo e un giroconto dello stesso giorno non sono
        il pagamento di un versamento: il verso e' sbagliato, o la riga e' gia'
        denaro spostato verso un altro conto."""
        self._importa()
        giro = self.session.scalar(select(Transaction).where(
            Transaction.occurred_on == date(2025, 2, 4),
            Transaction.transaction_type == "Transfers",
            Transaction.destination_name == "Conto2"))
        self.assertEqual(giro.account_name, "Conto")
        entrata = self.session.scalar(select(Transaction).where(
            Transaction.transaction_type == "Income", Transaction.account_name == "Conto"))
        self.assertEqual(entrata.amount, Decimal("300.00"))
        # Quattro versamenti convertiti, i due da fuori (uno entra, uno esce) e
        # l'esca: sette giroconti in tutto.
        self.assertEqual(len(self.session.scalars(select(Transaction).where(
            Transaction.transaction_type == "Transfers")).all()), 7)

    def test_il_conto_non_tracciato_paga_solo_quello_che_non_ha_riga(self) -> None:
        self._importa()
        fuori = self.session.scalars(select(Transaction).where(
            Transaction.account_name == "Versamenti non tracciati")).all()
        self.assertEqual([r.amount for r in fuori], [Decimal("500.00")])
        self.assertEqual((fuori[0].transaction_type, fuori[0].destination_name),
                         ("Transfers", "Scalable"))
        # E il contrario: la restituzione da 100 esce dal broker e va li'.
        rientro = self.session.scalar(select(Transaction).where(
            Transaction.destination_name == "Versamenti non tracciati"))
        self.assertEqual((rientro.transaction_type, rientro.account_name, rientro.amount),
                         ("Transfers", "Scalable", Decimal("100.00")))

    def test_la_scelta_a_mano_di_una_riga_cambia_il_conto_non_tracciato(self) -> None:
        piano = self._piano()
        senza = self._senza_riga()
        scelta = main.ScalableAggancio(riga=senza["riga"],
                                       transaction_id=senza["candidati"][0].id)
        self._importa(agganci=[scelta])
        conti = self._conti()
        # Agganciato il versamento da 500, sul conto non tracciato resta solo
        # l'uscita da 100, che nessuna riga di banca spiega.
        self.assertEqual(conti["Versamenti non tracciati"].current_balance, Decimal("100.00"))
        self.assertEqual(conti["Scalable"].current_balance, Decimal("1722.34"))
        righe = self.session.scalars(select(Transaction).where(
            Transaction.destination_name == "Versamenti non tracciati")).all()
        self.assertEqual([r.amount for r in righe], [Decimal("100.00")])

    def test_una_riga_di_banca_scelta_a_mano_deve_avere_lo_stesso_importo(self) -> None:
        """Agganciare un versamento a una riga di un altro importo scriverebbe un
        giroconto che non e' quello che e' successo."""
        piano = self._piano()
        senza = self._senza_riga()
        presa = piano["movimenti"][0]["banca"].id
        with self.assertRaises(HTTPException) as errore:
            self._importa(agganci=[main.ScalableAggancio(riga=senza["riga"], transaction_id=presa)])
        self.assertEqual(errore.exception.detail["code"], "scalableLinkInvalid")

    def test_due_versamenti_non_si_prendono_la_stessa_riga_di_banca(self) -> None:
        """Due scelte a mano sulla stessa riga conterebbero due volte la stessa
        uscita: la seconda si rifiuta."""
        piano = self._piano()
        primo, secondo = [r for r in piano["movimenti"] if r["importo"] == Decimal("300.00")]
        presa = primo["banca"].id
        with self.assertRaises(HTTPException) as errore:
            self._importa(agganci=[main.ScalableAggancio(riga=primo["riga"], transaction_id=presa),
                                   main.ScalableAggancio(riga=secondo["riga"], transaction_id=presa)])
        self.assertEqual(errore.exception.detail["code"], "scalableLinkReused")

    def test_il_secondo_import_non_raddoppia_niente(self) -> None:
        self._importa()
        with self.assertRaises(HTTPException) as errore:
            self._importa()
        self.assertEqual(errore.exception.status_code, 409)
        self.assertEqual(errore.exception.detail, "scalableAlreadyImported")
        self.assertEqual(len(self.session.scalars(select(InvestmentTransaction)).all()), 4)

    def test_l_import_registra_il_suo_lotto(self) -> None:
        self._importa()
        lotto = self.session.scalar(select(ImportBatch).where(ImportBatch.kind == "scalable"))
        self.assertEqual(lotto.rows_accepted, 11)
        self.assertEqual(lotto.rows_rejected, 7)
        self.assertEqual(lotto.account_count, 3)

    def test_su_un_altro_utente_non_si_scrive(self) -> None:
        payload = main.ScalableImportPayload(testo=TESTO, utente=UTENTE + 1)
        with self.assertRaises(HTTPException) as errore:
            main.import_scalable(payload, self.session)
        self.assertEqual(errore.exception.detail, "scalableUserMismatch")

    def test_un_elenco_senza_niente_da_importare_si_rifiuta(self) -> None:
        with self.assertRaises(HTTPException) as errore:
            main._piano_scalable(self.session, "lunedì, 3 febbraio 2025\n")
        self.assertEqual(errore.exception.detail, "scalableNothingToImport")

    # -- quadratura ----------------------------------------------------------

    def test_i_saldi_dei_conti_tornano_dai_loro_movimenti(self) -> None:
        self._importa()
        movimenti = self.session.scalars(select(Transaction)).all()
        for nome in ("Scalable", "Scalable investimenti", "Versamenti non tracciati"):
            conto = self.session.scalar(select(Account).where(Account.name == nome))
            letto = calculate_account_balance(Decimal("0"), nome, movimenti)
            self.assertEqual(letto, conto.current_balance.quantize(Decimal("0.01")), nome)

    def test_ogni_gruppo_del_ledger_quadra(self) -> None:
        self._importa()
        for legame in self.session.scalars(select(TransactionLedgerLink)).all():
            self.assertTrue(main.quadratura_gruppo(self.session, legame.transaction_id)["balanced"])


if __name__ == "__main__":
    unittest.main()
