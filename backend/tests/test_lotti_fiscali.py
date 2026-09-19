"""I lotti: quali quote sono state vendute, e quanto erano costate.

Motore puro su righe dict, nessun database, numeri tondi e inventati.

Il test che conta piu' di tutti e' ``InvariantiTests``: dopo ogni caso, e per
entrambi i metodi a lotti, le quote dei lotti consumati devono tornare col
saldo della posizione e i loro costi col costo della posizione. E' quello che
prende l'intera classe di errori - uno split che non riscala i lotti, una
vendita che ne consuma meta' senza dividere il costo, un lotto a zero quote che
resta in coda - e sono errori che **non si vedono**: il cruscotto mostrerebbe
numeri plausibili con un costo che non e' quello di nessun lotto.

Il secondo e' ``TotaleIndipendenteDalMetodoTests``: il metodo sposta la
frontiera fra realizzato e non realizzato, non il totale. Se un giorno non
fosse piu' vero, il cruscotto direbbe che il metodo ha cambiato quanto si e'
guadagnato.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from app.calculation_engine import investment_positions

GEN = date(2026, 1, 15)
FEB = date(2026, 2, 10)
MAR = date(2026, 3, 10)

NOMI = {"media", "fifo", "lifo"}


class Storia:
    """Una storia di operazioni con id progressivi, come li da' il database.

    Gli id contano: fifo e lifo sono sensibili all'ordine, e due righe dello
    stesso giorno si distinguono solo con quello.
    """

    def __init__(self) -> None:
        self.righe: list[dict] = []
        self.fee: dict[int, Decimal] = {}

    def operazione(self, giorno: date, tipo: str, quote: str, importo: str, *,
                   fee: str | None = None, prezzo: str | None = None,
                   id: int | None = None) -> Storia:
        riga = {"occurred_on": giorno, "transaction_type": tipo,
                "units": Decimal(quote), "amount": Decimal(importo),
                "name": "Titolo", "ticker": "TIT.MI"}
        if prezzo is not None:
            riga["price"] = Decimal(prezzo)
        if id is not None:
            riga["id"] = id
        else:
            riga["id"] = len(self.righe) + 1
        self.righe.append(riga)
        if fee is not None:
            self.fee[riga["id"]] = Decimal(fee)
        return self

    def posizioni(self, metodo: str = "media") -> list[dict]:
        return investment_positions(self.righe, fees_by_transaction=self.fee,
                                    cost_basis_method=metodo)

    def posizione(self, metodo: str = "media") -> dict:
        return next(p for p in self.posizioni(metodo) if p["name"] == "Titolo")

    def numeri(self) -> dict[str, tuple[Decimal, Decimal, Decimal, Decimal]]:
        """Quote, costo, realizzato e guadagno totale, per ogni metodo."""
        return {metodo: (self.posizione(metodo)["units"], self.posizione(metodo)["cost_basis"],
                         self.posizione(metodo)["realized_gain"], self.posizione(metodo)["total_gain"])
                for metodo in NOMI}


class LottoTestCase(unittest.TestCase):
    """Le due verifiche che ogni caso deve passare, in un posto solo."""

    def verifica(self, storia: Storia) -> None:
        for metodo in ("fifo", "lifo"):
            posizione = storia.posizione(metodo)
            somma_quote = sum((lotto[0] for lotto in posizione["lots"]), Decimal("0"))
            somma_costi = sum((lotto[1] for lotto in posizione["lots"]), Decimal("0"))
            self.assertEqual(posizione["units"], somma_quote.quantize(Decimal("0.00000001")),
                             f"le quote dei lotti non tornano con la posizione ({metodo})")
            # Il costo si confronta quantizzato: una vendita parziale divide il
            # costo di un lotto, e il residuo di quella divisione resta nel
            # lotto con tutte le sue cifre. La posizione esce arrotondata al
            # centesimo, e i due valori sono lo stesso numero arrotondato allo
            # stesso modo.
            self.assertEqual(posizione["cost_basis"], somma_costi.quantize(Decimal("0.01")),
                             f"i costi dei lotti non tornano col costo della posizione ({metodo})")
        # Sotto media i lotti non si costruiscono affatto: e' il ramo di codice
        # di sempre, e questo e' il posto dove si vede che non e' cambiato.
        self.assertEqual([], storia.posizione("media")["lots"])


class MetodiTests(LottoTestCase):
    def test_fifo_e_lifo_vendono_lotti_diversi(self) -> None:
        # Due lotti, 1000 e 2000, e una vendita da 2600. Chi sceglie il metodo
        # sceglie **quale** dei due lotti e' stato venduto.
        storia = (Storia().operazione(GEN, "Buy", "10", "1000")
                          .operazione(FEB, "Buy", "10", "2000")
                          .operazione(MAR, "Sell", "10", "2600"))
        self.verifica(storia)
        media, fifo, lifo = (storia.posizione(m) for m in ("media", "fifo", "lifo"))
        # Il lotto comprato per primo, a 1000: il realizzato e' tutto li'.
        self.assertEqual(Decimal("2000.00"), fifo["cost_basis"])
        self.assertEqual(Decimal("1600.00"), fifo["realized_gain"])
        # Il piu' recente, a 2000: quasi tutto il ricavato se ne va nel costo.
        self.assertEqual(Decimal("1000.00"), lifo["cost_basis"])
        self.assertEqual(Decimal("600.00"), lifo["realized_gain"])
        # La media sta in mezzo, e le quote sono le stesse tre volte.
        self.assertEqual(Decimal("1500.00"), media["cost_basis"])
        self.assertEqual(Decimal("1100.00"), media["realized_gain"])
        self.assertEqual({Decimal("10.00000000")}, {p["units"] for p in (media, fifo, lifo)})

    def test_il_totale_non_cambia_col_metodo(self) -> None:
        # Il metodo sposta la frontiera fra realizzato e non realizzato, non il
        # guadagno. Con l'ultimo prezzo di scambio a 260, le dieci quote che
        # restano valgono 2600 in tutti e tre i casi.
        storia = (Storia().operazione(GEN, "Buy", "10", "1000")
                          .operazione(FEB, "Buy", "10", "2000")
                          .operazione(MAR, "Sell", "10", "2600", prezzo="260"))
        self.verifica(storia)
        totali = {metodo: storia.posizione(metodo)["total_gain"] for metodo in NOMI}
        self.assertEqual({Decimal("2200.00")}, set(totali.values()))
        # E i due pezzi invece si muovono, o il test sopra sarebbe vuoto.
        self.assertEqual(Decimal("1600.00"), storia.posizione("fifo")["realized_gain"])
        self.assertEqual(Decimal("600.00"), storia.posizione("fifo")["unrealized_gain"])

    def test_un_metodo_scritto_male_non_rompe_il_cruscotto(self) -> None:
        # L'impostazione si scrive dalla pagina e il salvataggio accetta
        # qualunque stringa: qui arriva davvero, e deve leggersi come "non ha
        # scelto niente" invece di far esplodere la lettura delle posizioni.
        storia = (Storia().operazione(GEN, "Buy", "10", "1000")
                          .operazione(FEB, "Buy", "10", "2000")
                          .operazione(MAR, "Sell", "10", "2600"))
        media = storia.posizione("media")
        for spazzatura in ("lotto", "", None, "  ", "costo-medio"):
            with self.subTest(valore=spazzatura):
                posizione = investment_positions(storia.righe, fees_by_transaction=storia.fee,
                                                 cost_basis_method=spazzatura)[0]
                self.assertEqual(media["cost_basis"], posizione["cost_basis"])
        # Maiuscole e spazi invece si leggono: "FIFO " e' FIFO, non un errore di
        # battitura da buttare via.
        self.assertEqual(storia.posizione("fifo")["cost_basis"],
                         investment_positions(storia.righe, fees_by_transaction=storia.fee,
                                              cost_basis_method=" FIFO ")[0]["cost_basis"])


class BordiTests(LottoTestCase):
    def test_vendita_oltre_le_quote_non_vende_l_aria(self) -> None:
        # Dieci quote, ne vende venti. Oggi non esisteva nessun test su questo:
        # il motore taglia a quello che c'e', e quello che non c'e' non si
        # incassa. Il costo va a zero, il ricavato no.
        storia = (Storia().operazione(GEN, "Buy", "10", "100")
                          .operazione(MAR, "Sell", "20", "260"))
        self.verifica(storia)
        for metodo in NOMI:
            posizione = storia.posizione(metodo)
            self.assertEqual(Decimal("0.00000000"), posizione["units"], metodo)
            self.assertEqual(Decimal("0.00"), posizione["cost_basis"], metodo)
            self.assertEqual(Decimal("160.00"), posizione["realized_gain"], metodo)

    def test_vendita_di_qualcosa_mai_comprato(self) -> None:
        # La posizione nasce dalla vendita e non ha nessun lotto: non c'e' costo
        # da scaricare, e il ciclo dei lotti non gira nemmeno una volta.
        storia = Storia().operazione(MAR, "Sell", "5", "300")
        self.verifica(storia)
        for metodo in NOMI:
            posizione = storia.posizione(metodo)
            self.assertEqual(Decimal("0.00000000"), posizione["units"], metodo)
            self.assertEqual(Decimal("0.00"), posizione["cost_basis"], metodo)
            self.assertEqual(Decimal("300.00"), posizione["realized_gain"], metodo)

    def test_uno_split_riscala_i_lotti_rimasti(self) -> None:
        # Il caso che il test dello split non copre, perche' li' il
        # frazionamento arriva su una posizione senza vendite in mezzo: qui i
        # lotti sono gia' stati consumati e ridotti, e devono raddoppiare le
        # quote senza cambiare i costi.
        storia = (Storia().operazione(GEN, "Buy", "100", "1000")
                          .operazione(FEB, "Buy", "100", "2000")
                          .operazione(MAR, "Sell", "40", "480")
                          .operazione(date(2026, 4, 1), "Split", "2", "0")
                          .operazione(date(2026, 5, 1), "Sell", "50", "300"))
        self.verifica(storia)
        fifo = storia.posizione("fifo")
        # Il primo lotto, ridotto a 60 quote da 600, raddoppia a 120 da 600 e la
        # vendita ne consuma 50: ne restano 70, sempre da 350.
        self.assertEqual([[Decimal("70"), Decimal("350")], [Decimal("200"), Decimal("2000")]],
                         fifo["lots"])
        self.assertEqual(Decimal("270.00000000"), fifo["units"])
        self.assertEqual(Decimal("2350.00"), fifo["cost_basis"])
        self.assertEqual(Decimal("130.00"), fifo["realized_gain"])
        # La media, sulla stessa storia, dice un'altra cosa: 15 di costo medio
        # sulle prime 40 quote, 7,50 sulle 50 dopo il raddoppio. E' il punto del
        # metodo, non un guasto.
        self.assertEqual(Decimal("2025.00"), storia.posizione("media")["cost_basis"])

    def test_le_commissioni_seguono_il_lotto(self) -> None:
        # La fee dell'acquisto entra nel lotto, quella della vendita riduce il
        # ricavato - e nessuna delle due tocca il costo per intero: e' il lotto
        # che si porta il suo pezzo di commissione.
        storia = (Storia().operazione(GEN, "Buy", "10", "1000", fee="5")
                          .operazione(MAR, "Sell", "4", "500", fee="3"))
        self.verifica(storia)
        fifo = storia.posizione("fifo")
        self.assertEqual([[Decimal("6"), Decimal("603")]], fifo["lots"])
        self.assertEqual(Decimal("603.00"), fifo["cost_basis"])
        # 497 incassati contro 402 di costo delle quattro quote.
        self.assertEqual(Decimal("95.00"), fifo["realized_gain"])

    def test_un_dividendo_non_tocca_i_lotti(self) -> None:
        # Gemello di quello sul costo medio: incassare non compra e non vende.
        storia = (Storia().operazione(GEN, "Buy", "10", "1000")
                          .operazione(FEB, "Dividend", "0", "30"))
        self.verifica(storia)
        fifo = storia.posizione("fifo")
        self.assertEqual([[Decimal("10"), Decimal("1000")]], fifo["lots"])
        self.assertEqual(Decimal("1000.00"), fifo["cost_basis"])
        self.assertEqual(Decimal("30.00"), fifo["income_received"])

    def test_una_commissione_staccata_non_compra_quote(self) -> None:
        # Il tipo Fee e' denaro uscito dal portafoglio, non un costo di carico:
        # non deve lasciare un lotto da zero quote che la prima vendita
        # dividerebbe.
        storia = (Storia().operazione(GEN, "Buy", "10", "1000")
                          .operazione(FEB, "Fee", "0", "12"))
        self.verifica(storia)
        self.assertEqual([[Decimal("10"), Decimal("1000")]], storia.posizione("fifo")["lots"])
        self.assertEqual(Decimal("1000.00"), storia.posizione("fifo")["cost_basis"])
        self.assertEqual(Decimal("12.00"), storia.posizione("fifo")["fees_paid"])


class OrdineTests(LottoTestCase):
    """Fifo e lifo sono sensibili all'ordine, e l'ordine ha un punto debole noto."""

    def test_due_righe_lo_stesso_giorno_si_distinguono_dall_id(self) -> None:
        # Stesso giorno, stesso strumento: l'unica differenza e' l'id, ed e'
        # quello che decide quale lotto esce per primo.
        storia = (Storia().operazione(GEN, "Buy", "10", "1000", id=1)
                          .operazione(GEN, "Buy", "10", "2000", id=2)
                          .operazione(MAR, "Sell", "10", "1200"))
        self.verifica(storia)
        # Il primo comprato, a 1000: realizzo 200. Con il lifo sarebbe il
        # secondo, e il realizzo sarebbe una perdita.
        self.assertEqual(Decimal("200.00"), storia.posizione("fifo")["realized_gain"])
        self.assertEqual(Decimal("-800.00"), storia.posizione("lifo")["realized_gain"])

    def test_senza_id_l_ordine_e_quello_di_arrivo(self) -> None:
        # Fragilita' nota, dichiarata invece che sistemata qui: le righe senza
        # id collassano su zero e `sorted` e' stabile, quindi resta l'ordine in
        # cui sono arrivate. E' quello che il database fa gia' con
        # `order by occurred_on, id`, e cambiarlo e' un altro discorso.
        storia = Storia()
        for riga in ({"occurred_on": GEN, "transaction_type": "Buy", "units": Decimal("10"),
                      "amount": Decimal("1000"), "name": "Titolo", "ticker": "TIT.MI"},
                     {"occurred_on": GEN, "transaction_type": "Buy", "units": Decimal("10"),
                      "amount": Decimal("2000"), "name": "Titolo", "ticker": "TIT.MI"},
                     {"occurred_on": MAR, "transaction_type": "Sell", "units": Decimal("10"),
                      "amount": Decimal("1200"), "name": "Titolo", "ticker": "TIT.MI"}):
            storia.righe.append(riga)
        self.verifica(storia)
        self.assertEqual(Decimal("200.00"), storia.posizione("fifo")["realized_gain"])


class SenzaDataTests(LottoTestCase):
    def test_una_riga_senza_data_finisce_in_testa(self) -> None:
        # `date.min` non e' un caso di scuola: e' quello che il motore mette a
        # una riga senza data, e quella riga e' la prima comprata. I lotti
        # devono seguire l'ordine che il motore usa per tutto il resto.
        storia = Storia()
        storia.righe.append({"occurred_on": None, "transaction_type": "Buy",
                             "units": Decimal("10"), "amount": Decimal("1000"),
                             "name": "Titolo", "ticker": "TIT.MI", "id": 1})
        storia.operazione(MAR, "Buy", "10", "2000", id=2)
        storia.operazione(date(2026, 6, 1), "Sell", "10", "1200", id=3)
        self.verifica(storia)
        self.assertEqual(Decimal("200.00"), storia.posizione("fifo")["realized_gain"])


if __name__ == "__main__":
    unittest.main()
