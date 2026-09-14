import logging
import csv
from typing import List, Dict, Any
from io import StringIO

from .statement_parsing import parse_amount as _shared_parse_amount, parse_date as _shared_parse_date

logger = logging.getLogger("money.import")


class CSVStatementParser:
    """Parser per estratti conto bancari in formato CSV"""

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
    def extract_transactions_from_csv(file_content: str, encoding: str = 'utf-8') -> List[Dict[str, Any]]:
        """
        Estrae le transazioni da un file CSV
        Restituisce una lista di dizionari compatibili con il modello Transaction
        """
        # Prova diversi delimitatori
        delimiters = [',', ';', '\t', '|']
        transactions = []

        for delimiter in delimiters:
            try:
                # Reset file pointer
                content = file_content
                reader = csv.DictReader(StringIO(content), delimiter=delimiter)
                rows = list(reader)

                if not rows:
                    continue

                # Verifica che ci siano colonne riconoscibili
                headers = rows[0].keys() if rows else []
                if not CSVStatementParser._has_transaction_columns(headers):
                    continue

                # Rileva formato bancario
                fmt = CSVStatementParser.detect_format(list(headers))
                format_config = CSVStatementParser.BANK_FORMATS[fmt]

                # Mappa le colonne
                column_mapping = CSVStatementParser._map_columns(headers, format_config)

                # Processa le righe
                parsed = CSVStatementParser._parse_rows(rows, column_mapping, format_config)
                if parsed:
                    transactions = parsed
                    break

            except Exception:
                continue

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
    def _parse_rows(rows: List[Dict], column_mapping: Dict[str, int], format_config: Dict) -> List[Dict[str, Any]]:
        """Converte le righe CSV in transazioni"""
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

                if amount == 0:
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
