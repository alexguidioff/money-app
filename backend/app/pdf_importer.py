import logging
import pdfplumber
from typing import List, Dict, Any

from .statement_parsing import parse_amount as _shared_parse_amount, parse_date as _shared_parse_date

logger = logging.getLogger("money.import")

class BankStatementParser:
    """Parser per estratti conto bancari in formato PDF"""
    
    @staticmethod
    def extract_transactions_from_pdf(file_path: str) -> List[Dict[str, Any]]:
        """
        Estrae le transazioni da un estratto conto bancario PDF
        Restituisce una lista di dizionari compatibili con il modello Transaction
        """
        transactions = []
        
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                # Estrai tabelle dalla pagina
                tables = page.extract_tables()
                
                for table in tables:
                    if BankStatementParser._is_transaction_table(table):
                        parsed = BankStatementParser._parse_transaction_table(table)
                        transactions.extend(parsed)
        
        return transactions
    
    @staticmethod
    def _is_transaction_table(table: List[List[str]]) -> bool:
        """Verifica se una tabella contiene dati di transazione"""
        if not table or len(table) < 2:
            return False
            
        # Cerca intestazioni tipiche delle transazioni bancarie
        header_row = [str(cell).lower() for cell in table[0] if cell]
        transaction_indicators = {
            'data', 'date', 'descrizione', 'description', 
            'importo', 'amount', 'addebito', 'debit', 
            'accredito', 'credit', 'causale', 'causale'
        }
        
        return any(indicator in ' '.join(header_row) for indicator in transaction_indicators)
    
    @staticmethod
    def _parse_transaction_table(table: List[List[str]]) -> List[Dict[str, Any]]:
        """Converte una tabella di transazioni in formato interno"""
        if not table:
            return []
            
        # Identifica le colonne dalla prima riga (intestazione)
        headers = [str(h).lower().strip() if h else '' for h in table[0]]
        transactions = []
        
        # Mappa le colonne comuni dei estratti conto
        column_mapping = {
            'data': ['data', 'date', 'giorno', 'day'],
            'descrizione': ['descrizione', 'description', 'causale', 'narrazione', 'particulars'],
            'importo': ['importo', 'amount', 'ammontare', 'valor'],
            'addebito': ['addebito', 'debit', 'uscita', 'outgoing'],
            'accredito': ['accredito', 'credit', 'entrata', 'incoming'],
            'saldo': ['saldo', 'balance', 'disponibile', 'available']
        }
        
        # Trova gli indici delle colonne
        column_indices = {}
        for std_name, variants in column_mapping.items():
            for i, header in enumerate(headers):
                if any(variant in header for variant in variants):
                    column_indices[std_name] = i
                    break
        
        # Processa le righe di dati (salta l'intestazione)
        for row in table[1:]:
            if not any(row):  # Salta righe vuote
                continue
                
            try:
                transaction = {}
                
                # Estrai data
                if 'data' in column_indices:
                    date_str = str(row[column_indices['data']]).strip()
                    transaction['occurredOn'] = BankStatementParser._parse_date(date_str)
                
                # Estrai descrizione
                if 'descrizione' in column_indices:
                    transaction['description'] = str(row[column_indices['descrizione']]).strip()
                
                # Calcola importo (gestisce sia formato unico che addebito/accredito separati)
                amount = 0
                if 'importo' in column_indices:
                    amount_str = str(row[column_indices['importo']]).strip()
                    amount = BankStatementParser._parse_amount(amount_str)
                else:
                    # Formato addebito/accredito separati
                    debit_amount = 0
                    credit_amount = 0
                    if 'addebito' in column_indices:
                        debit_str = str(row[column_indices['addebito']]).strip()
                        debit_amount = BankStatementParser._parse_amount(debit_str)
                    if 'accredito' in column_indices:
                        credit_str = str(row[column_indices['accredito']]).strip()
                        credit_amount = BankStatementParser._parse_amount(credit_str)
                    amount = credit_amount - debit_amount  # Credito positivo, addebito negativo
                
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
                
                if transaction['description']:
                    transactions.append(transaction)
                    
            except Exception as e:
                # Log error but continue processing other rows
                logger.warning("riga PDF non interpretabile, saltata: %s", e)
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
