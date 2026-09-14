import logging
import re
from statistics import median
from typing import List, Dict, Any

import pdfplumber

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
            # Molte banche non disegnano la tabella: il testo e' solo allineato
            # in colonne. Se non c'erano tabelle vere, si leggono le colonne.
            if not transactions:
                for page in pdf.pages:
                    transactions.extend(movimenti_da_colonne(page.extract_words()))
        
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


# --- Estratti conto senza tabella disegnata -----------------------------------
#
# Il testo e' allineato sotto un'intestazione ("Data  Descrizione  Importo",
# oppure "DATA  TIPO  DESCRIZIONE  IN ENTRATA  IN USCITA  SALDO"). Ogni movimento
# ha una riga con l'importo; data e descrizione possono andare a capo sopra e
# sotto quella riga, centrate su di lei. Quindi: si trovano le righe con un
# importo, e ogni altra parola va al movimento piu' vicino in verticale, nella
# colonna sotto cui sta.

COLONNE = [  # (tipo, parole dell'intestazione), il primo che combacia vince
    ('entrata', ('entrata', 'accredit', 'avere', 'credit', 'incoming', 'haben', 'crédit', 'ingreso')),
    ('uscita', ('uscita', 'addebit', 'dare', 'debit', 'outgoing', 'soll', 'débit', 'cargo')),
    ('saldo', ('saldo', 'balance', 'solde')),
    ('importo', ('importo', 'amount', 'betrag', 'montant', 'importe')),
    ('descrizione', ('descrizione', 'description', 'causale', 'beschreibung', 'verwendungszweck',
                     'libellé', 'libelle', 'descripción', 'concepto')),
    ('data', ('data', 'date', 'datum', 'fecha')),
]
IMPORTO = re.compile(r'^[-+(]?\d{1,3}(?:[.,\s]?\d{3})*[.,]\d{2}\)?-?$')
ATTACCATE = 4  # punti: parole piu' vicine di cosi' sono la stessa etichetta


def _righe(parole: list[dict]) -> list[list[dict]]:
    righe: list[list[dict]] = []
    for parola in sorted(parole, key=lambda p: (p['top'], p['x0'])):
        if righe and abs(righe[-1][0]['top'] - parola['top']) <= 2:
            righe[-1].append(parola)
        else:
            righe.append([parola])
    return [sorted(riga, key=lambda p: p['x0']) for riga in righe]


def _intestazione(riga: list[dict]) -> dict[str, tuple[float, float]] | None:
    """Le colonne di una riga d'intestazione, o None se non lo e'."""
    etichette: list[list[dict]] = []
    for parola in riga:
        if etichette and parola['x0'] - etichette[-1][-1]['x1'] <= ATTACCATE:
            etichette[-1].append(parola)
        else:
            etichette.append([parola])
    colonne: dict[str, tuple[float, float]] = {'_etichette': tuple(e[0]['x0'] for e in etichette)}
    for etichetta in etichette:
        testo = ' '.join(p['text'] for p in etichetta).lower()
        tipo = next((t for t, chiavi in COLONNE if any(c in testo for c in chiavi)), None)
        if tipo and tipo not in colonne:
            colonne[tipo] = (etichetta[0]['x0'], etichetta[-1]['x1'])
    importi = {'importo', 'entrata', 'uscita'} & colonne.keys()
    return colonne if {'data', 'descrizione'} <= colonne.keys() and importi else None


def _dentro(parola: dict, inizio: float, fine: float) -> bool:
    return inizio - 5 <= parola['x0'] < fine


def movimenti_da_colonne(parole: list[dict]) -> list[dict[str, Any]]:
    """I movimenti di una pagina il cui testo e' allineato in colonne."""
    righe = _righe(parole)
    inizio = next((i for i, riga in enumerate(righe) if _intestazione(riga)), None)
    if inizio is None:
        return []
    colonne = _intestazione(righe[inizio])
    etichette = colonne.pop('_etichette')
    x_descrizione = colonne['descrizione'][0]
    x_importi = min(x0 for tipo, (x0, _) in colonne.items() if tipo in {'importo', 'entrata', 'uscita', 'saldo'})
    # La data finisce dove comincia l'etichetta successiva, anche se e' una
    # colonna che non si legge (tipo, codice): altrimenti ci finirebbe dentro.
    x_data = colonne['data'][0]
    fine_data = min((x0 for x0 in etichette if x0 > x_data), default=x_descrizione)
    centro = lambda p: (p['x0'] + p['x1']) / 2

    def colonna_importo(parola: dict) -> str | None:
        if parola['x1'] < x_importi - 20 or not IMPORTO.match(parola['text']):
            return None
        vicine = [(abs(centro(parola) - (x0 + x1) / 2), tipo) for tipo, (x0, x1) in colonne.items()
                  if tipo in {'importo', 'entrata', 'uscita', 'saldo'}]
        return min(vicine)[1]

    corpo: list[list[dict]] = []
    for riga in righe[inizio + 1:]:
        prima = riga[0]
        # Un titolo in maiuscolo nella colonna della data chiude l'elenco: dopo
        # vengono riepiloghi con altre colonne (fondi, note, avvertenze).
        if prima['x0'] < fine_data and prima['text'].isalpha() and prima['text'].isupper() and len(prima['text']) > 3:
            break
        corpo.append(riga)

    ancore = []
    for riga in corpo:
        importi = {colonna_importo(p): p['text'] for p in riga}
        valore = None
        if 'importo' in importi:
            valore = _parse_amount(importi['importo'])
        elif 'entrata' in importi:
            valore = abs(_parse_amount(importi['entrata']))
        elif 'uscita' in importi:
            valore = -abs(_parse_amount(importi['uscita']))
        if valore:
            ancore.append({'top': riga[0]['top'], 'importo': valore, 'data': [], 'descrizione': []})
    if not ancore:
        return []
    passi = [b['top'] - a['top'] for a, b in zip(ancore, ancore[1:])]
    distanza = max(median(passi) * 0.6, 8) if passi else 30

    for riga in corpo:
        for parola in riga:
            if colonna_importo(parola):
                continue
            vicina = min(ancore, key=lambda a: abs(a['top'] - parola['top']))
            if abs(vicina['top'] - parola['top']) > distanza:
                continue
            if _dentro(parola, x_data, fine_data):
                vicina['data'].append(parola)
            elif x_descrizione - 5 <= parola['x0'] and parola['x1'] < x_importi:
                vicina['descrizione'].append(parola)

    movimenti = []
    for ancora in ancore:
        testo = lambda chiave: ' '.join(p['text'] for p in sorted(ancora[chiave], key=lambda p: (round(p['top']), p['x0'])))
        descrizione = testo('descrizione')
        if not descrizione:
            continue
        movimenti.append(_movimento(_parse_date(testo('data')), descrizione, ancora['importo']))
    return movimenti


def _movimento(data: str | None, descrizione: str, importo: float) -> dict[str, Any]:
    return {
        'occurredOn': data, 'description': descrizione,
        'amount': importo, 'rawAmount': abs(importo),
        'type': 'income' if importo > 0 else 'expense',
        'transactionType': 'Income' if importo > 0 else 'Expenses',
        'category': 'Da categorizzare', 'categoryRaw': 'Other',
        'accountName': None, 'destinationName': None, 'goal': None, 'details': '',
    }


_parse_date = _shared_parse_date
_parse_amount = _shared_parse_amount
