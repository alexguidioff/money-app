import logging
import csv
from typing import List, Dict, Any
from io import StringIO

from .statement_parsing import parse_amount as _shared_parse_amount, parse_date as _shared_parse_date

logger = logging.getLogger("money.import")


class CSVStatementParser:
    """Parser per estratti conto bancari in formato CSV"""

    # I separatori fra colonne che si provano, in ordine: virgola, punto e
    # virgola, tabulazione, barra verticale.
    DELIMITATORI = (',', ';', '\t', '|')

    # Formati comuni per banche italiane
    BANK_FORMATS = {
        'generic': {
            'date_cols': ['data', 'date', 'giorno', 'day', 'data operazione', 'data valuta'],
            'desc_cols': ['descrizione', 'description', 'causale', 'narrazione', 'particulars', 'dettaglio'],
            'amount_cols': ['importo', 'amount', 'ammontare', 'valor', 'addebito', 'accredito'],
            'debit_cols': ['addebito', 'debit', 'uscita', 'outgoing', 'prelievo'],
            'credit_cols': ['accredito', 'credit', 'entrata', 'incoming', 'versamento'],
            'balance_cols': ['saldo', 'balance', 'disponibile', 'available'],
        },
        'intesa': {
            'date_cols': ['data operazione', 'data valuta'],
            'desc_cols': ['descrizione operazione', 'descrizione'],
            'amount_cols': ['importo'],
            'debit_cols': [],
            'credit_cols': [],
            'balance_cols': ['saldo contabile'],
        },
        'unicredit': {
            'date_cols': ['data', 'data valuta'],
            'desc_cols': ['descrizione', 'causale'],
            'amount_cols': ['importo'],
            'debit_cols': ['addebito'],
            'credit_cols': ['accredito'],
            'balance_cols': ['saldo'],
        },
        'bpomilano': {
            'date_cols': ['data contabile', 'data valuta'],
            'desc_cols': ['descrizione', 'causale'],
            'amount_cols': ['importo'],
            'debit_cols': ['addebito'],
            'credit_cols': ['accredito'],
            'balance_cols': ['saldo'],
        },
        'fineco': {
            'date_cols': ['data', 'data valuta'],
            'desc_cols': ['descrizione', 'note'],
            'amount_cols': ['importo'],
            'debit_cols': ['addebito'],
            'credit_cols': ['accredito'],
            'balance_cols': ['saldo'],
        },
        'ing': {
            'date_cols': ['datum', 'date', 'valuta'],
            'desc_cols': ['beschreibung', 'description', 'causale'],
            'amount_cols': ['betrag', 'amount', 'importo'],
            'debit_cols': ['abgang', 'addebito'],
            'credit_cols': ['zugang', 'accredito'],
            'balance_cols': ['saldo', 'balance'],
        },
        'revolut': {
            'date_cols': ['started date', 'completed date', 'date'],
            'desc_cols': ['description', 'merchant', 'causale'],
            'amount_cols': ['amount', 'importo'],
            'debit_cols': [],
            'credit_cols': [],
            'balance_cols': ['balance'],
        },
        'wise': {
            'date_cols': ['date', 'time'],
            'desc_cols': ['description', 'merchant', 'reference'],
            'amount_cols': ['amount', 'importo'],
            'debit_cols': [],
            'credit_cols': [],
            'balance_cols': ['running balance', 'balance'],
        },
    }

    @staticmethod
    def detect_format(headers: List[str]) -> str:
        """Rileva il formato bancario dagli header"""
        headers_lower = [h.lower().strip() for h in headers]

        # Score per ogni formato
        scores = {}
        for fmt_name, fmt_config in CSVStatementParser.BANK_FORMATS.items():
            score = 0
            for col_type, col_names in fmt_config.items():
                for col_name in col_names:
                    for header in headers_lower:
                        if col_name in header:
                            score += 1
            scores[fmt_name] = score

        # Restituisce il formato con score più alto (almeno 2 match)
        best_format = max(scores, key=scores.get)
        return best_format if scores[best_format] >= 2 else 'generic'

    @staticmethod
    def _candidati(file_content: str, delimitatori: tuple = DELIMITATORI):
        """Il file letto con ciascun delimitatore possibile, in ordine di prova.

        Un solo posto sceglie delimitatore, formato bancario e mappatura: la
        schermata che fa scegliere le colonne e l'import devono leggere le
        stesse colonne, altrimenti la mappatura mostrata a chi importa non
        sarebbe quella che l'import usa davvero.

        Restituisce le intestazioni senza le chiavi spurie (una riga con piu'
        campi delle intestazioni ne produce una), ma la mappatura la calcola su
        quelle grezze: gli indici sono posizioni nelle righe, non nell'elenco
        che si mostra.
        """
        for delimiter in delimitatori:
            try:
                rows = list(csv.DictReader(StringIO(file_content), delimiter=delimiter))
                if not rows:
                    continue
                grezze = list(rows[0].keys())
                config = CSVStatementParser.BANK_FORMATS[CSVStatementParser.detect_format(grezze)]
                mappatura = CSVStatementParser._map_columns(grezze, config)
            except Exception:
                # Un delimitatore sbagliato si riconosce cosi': le intestazioni
                # che ne escono non sono intestazioni.
                continue
            yield delimiter, [header for header in grezze if header is not None], rows, mappatura, config

    @staticmethod
    def colonne(file_content: str) -> Dict[str, Any]:
        """Intestazioni, prime righe, mappatura proposta e delimitatore.

        La proposta e' quella che l'import userebbe da solo: l'euristica non
        sparisce, diventa un suggerimento che si puo' correggere. Un file che
        l'euristica non riconosce torna lo stesso, con le sue intestazioni e
        senza proposta - ed e' proprio il caso in cui serve poterle scegliere.

        Il delimitatore torna insieme alle colonne perche' senza di esso gli
        indici scelti non vorrebbero dire niente: e' quello il taglio su cui
        sono stati contati, e l'import deve rileggere il file allo stesso modo.
        """
        candidati = list(CSVStatementParser._candidati(file_content))
        # Fra le letture che sembrano un estratto conto si tiene quella che
        # spezza in piu' colonne: con il separatore sbagliato l'intestazione
        # intera resta una colonna sola, e l'euristica la riconosce lo stesso
        # perche' dentro c'e' scritto "Data". Mostrarla cosi' vorrebbe dire
        # togliere a chi importa proprio la possibilita' di correggerla.
        noti = [candidato for candidato in candidati if CSVStatementParser._has_transaction_columns(candidato[1])]
        scelto = max(noti or candidati, key=lambda candidato: len(candidato[1]), default=None)
        if scelto is None:
            return {"headers": [], "sample": [], "mapping": {}, "delimiter": ","}
        return {"headers": scelto[1], "sample": CSVStatementParser._campione(scelto[2]),
                "mapping": scelto[3] if noti else {}, "delimiter": scelto[0]}

    @staticmethod
    def _campione(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Le prime righe come sono nel file, per farle vedere."""
        return [{str(chiave): (valore or '') for chiave, valore in row.items() if chiave is not None}
                for row in rows[:5]]

    @staticmethod
    def extract_transactions_from_csv(file_content: str, encoding: str = 'utf-8',
                                      mapping: Dict[str, int] | None = None,
                                      delimiter: str | None = None) -> List[Dict[str, Any]]:
        """
        Estrae le transazioni da un file CSV
        Restituisce una lista di dizionari compatibili con il modello Transaction

        ``mapping`` e' la mappatura scelta a mano (quale indice di colonna e' la
        data, la descrizione, l'importo): se c'e' vince sull'euristica, se manca
        il comportamento e' quello di sempre. Con una mappatura scelta a mano
        una riga che non si legge non sparisce in silenzio: esce segnata, e chi
        importa decide.

        ``delimiter`` accompagna la mappatura: gli indici sono posizioni nel
        taglio da cui sono stati scelti, e senza dirlo si leggerebbe un altro
        taglio. Se manca si prova come al solito.
        """
        # Prova diversi delimitatori
        transactions = []
        delimitatori = tuple(delimiter) if delimiter else CSVStatementParser.DELIMITATORI

        for _, headers, rows, proposta, config in CSVStatementParser._candidati(file_content, delimitatori):
            # Con una mappatura scelta a mano le intestazioni non contano: le ha
            # gia' lette chi ha scelto, ed e' proprio il file che l'euristica non
            # riconosce quello per cui serve.
            if mapping is None and not CSVStatementParser._has_transaction_columns(headers):
                continue

            try:
                column_mapping = proposta if mapping is None else mapping
                parsed = CSVStatementParser._parse_rows(rows, column_mapping, config, segnala_scarti=mapping is not None)
            except Exception:
                continue
            if parsed:
                transactions = parsed
                break

        return transactions

    @staticmethod
    def _has_transaction_columns(headers) -> bool:
        """Verifica se le colonne sembrano dati transazionali"""
        if not headers:
            return False
        headers_str = ' '.join([str(h).lower() for h in headers if h])
        indicators = ['data', 'date', 'descrizione', 'description', 'importo', 'amount', 'addebito', 'accredito', 'causale']
        return any(ind in headers_str for ind in indicators)

    @staticmethod
    def _map_columns(headers, format_config: Dict) -> Dict[str, int]:
        """Mappa le colonne del CSV alle colonne standard"""
        column_indices = {}
        headers_list = list(headers)

        for std_name, variants in format_config.items():
            for i, header in enumerate(headers_list):
                header_lower = str(header).lower().strip()
                if any(variant in header_lower for variant in variants):
                    column_indices[std_name] = i
                    break

        return column_indices

    @staticmethod
    def _parse_rows(rows: List[Dict], column_mapping: Dict[str, int], format_config: Dict,
                    segnala_scarti: bool = False) -> List[Dict[str, Any]]:
        """Converte le righe CSV in transazioni.

        ``segnala_scarti`` si accende quando la mappatura l'ha scelta chi
        importa: allora una riga che non si legge e' un'informazione da
        mostrare - ``errorCode``, lo stesso campo con cui l'anteprima segnala le
        righe scartate al salvataggio, per non avere due vocabolari - invece di
        una riga che sparisce senza dirlo. Senza mappatura restano fuori come
        sempre.
        """
        transactions = []

        for row in rows:
            if not any(row.values()):
                continue

            try:
                transaction = {}
                headers = list(row.keys())

                # Estrai data dalla colonna 'date_cols'
                if 'date_cols' in column_mapping:
                    idx = column_mapping['date_cols']
                    if 0 <= idx < len(headers):
                        date_str = str(row.get(headers[idx], '')).strip()
                        transaction['occurredOn'] = CSVStatementParser._parse_date(date_str)

                # Estrai descrizione dalla colonna 'desc_cols'
                if 'desc_cols' in column_mapping:
                    idx = column_mapping['desc_cols']
                    if 0 <= idx < len(headers):
                        transaction['description'] = str(row.get(headers[idx], '')).strip()

                # Calcola importo
                amount = 0
                has_debit_credit = ('debit_cols' in column_mapping) or ('credit_cols' in column_mapping)
                if has_debit_credit:
                    # Formato addebito/accredito separati
                    debit_amount = 0
                    credit_amount = 0
                    if 'debit_cols' in column_mapping:
                        idx = column_mapping['debit_cols']
                        if 0 <= idx < len(headers):
                            debit_str = str(row.get(headers[idx], '')).strip()
                            debit_amount = CSVStatementParser._parse_amount(debit_str)
                    if 'credit_cols' in column_mapping:
                        idx = column_mapping['credit_cols']
                        if 0 <= idx < len(headers):
                            credit_str = str(row.get(headers[idx], '')).strip()
                            credit_amount = CSVStatementParser._parse_amount(credit_str)
                    amount = credit_amount - debit_amount
                elif 'amount_cols' in column_mapping:
                    idx = column_mapping['amount_cols']
                    if 0 <= idx < len(headers):
                        amount_str = str(row.get(headers[idx], '')).strip()
                        amount = CSVStatementParser._parse_amount(amount_str)

                # Perche' la riga non si legge, se non si legge: e' la stessa
                # domanda a cui risponde il salvataggio quando rifiuta una riga.
                # Si dice solo con una mappatura scelta a mano: senza, il
                # comportamento resta quello di sempre, anche nei suoi silenzi.
                motivo = ('statementDateInvalid' if not transaction.get('occurredOn')
                          else 'statementAmountInvalid' if amount == 0 else None) if segnala_scarti else None

                if amount == 0 and not segnala_scarti:
                    continue
                transaction['amount'] = amount
                transaction['rawAmount'] = abs(amount)
                transaction['type'] = 'income' if amount > 0 else 'expense'
                transaction['transactionType'] = 'Income' if amount > 0 else 'Expenses'

                # Imposta valori di default
                transaction['category'] = 'Da categorizzare'
                transaction['categoryRaw'] = 'Other'
                transaction['accountName'] = None
                transaction['destinationName'] = None
                transaction['goal'] = None
                transaction['details'] = ''

                if transaction.get('description'):
                    if motivo:
                        transaction['errorCode'] = motivo
                    transactions.append(transaction)

            except Exception as e:
                logger.warning("riga CSV non interpretabile, saltata: %s", e)
                continue

        return transactions

    @staticmethod
    def _parse_date(date_str: str) -> str | None:
        """Converte varie forme di data in formato ISO (YYYY-MM-DD), o None se non riconoscibile."""
        return _shared_parse_date(date_str)

    @staticmethod
    def _parse_amount(amount_str: str) -> float:
        """Converte stringhe di importo in float (delegato al parser condiviso)."""
        return _shared_parse_amount(amount_str)
