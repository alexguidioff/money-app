"""Le difese al confine dell'API: ognuna chiude un modo concreto di entrare."""

from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import patch

from fastapi import HTTPException, Response
from openpyxl import load_workbook
from starlette.requests import Request

from app import auth
from app.reports import excel_report


def _richiesta(metodo: str = "GET", host: str = "127.0.0.1:3010", origin: str | None = None) -> Request:
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request({"type": "http", "method": metodo, "path": "/api/summary", "headers": headers})


class OrigineTests(unittest.TestCase):
    def test_l_app_aperta_da_localhost_passa(self) -> None:
        self.assertTrue(auth._richiesta_dall_app(_richiesta()))
        self.assertTrue(auth._richiesta_dall_app(_richiesta("POST", "localhost:3010", "http://localhost:3010")))
        self.assertTrue(auth._richiesta_dall_app(_richiesta("POST", "api:8000")))

    def test_un_dominio_che_punta_a_127_viene_respinto(self) -> None:
        self.assertFalse(auth._richiesta_dall_app(_richiesta(host="attacco.example:3010")))

    def test_un_altro_sito_non_puo_scrivere(self) -> None:
        self.assertFalse(auth._richiesta_dall_app(_richiesta("POST", origin="https://attacco.example")))
        self.assertFalse(auth._richiesta_dall_app(_richiesta("DELETE", origin="null")))

    def test_l_indirizzo_configurato_e_ammesso(self) -> None:
        with patch.dict("os.environ", {"MONEY_APP_ORIGIN": "https://money.tailnet.ts.net"}):
            self.assertTrue(auth._richiesta_dall_app(
                _richiesta("POST", "money.tailnet.ts.net", "https://money.tailnet.ts.net")))


class NomeUtenteTests(unittest.TestCase):
    def test_un_nome_con_percorso_viene_rifiutato_prima_di_scrivere(self) -> None:
        for nome in ("../../app/x", "a/b", "mario.rossi", ""):
            with self.subTest(nome=nome), self.assertRaises(HTTPException) as errore:
                auth.crea_utente(auth.UserPayload(username=nome, display_name="Mario"), Response(), session=None)
            self.assertEqual(errore.exception.status_code, 422)


class TentativiTests(unittest.TestCase):
    def tearDown(self) -> None:
        auth._tentativi.clear()

    def test_dopo_troppi_errori_il_login_si_ferma(self) -> None:
        auth._tentativi["mario"] = [__import__("time").monotonic()] * auth.TENTATIVI_MAX
        with self.assertRaises(HTTPException) as errore:
            auth.entra(auth.LoginPayload(username="Mario", password="x"), Response(), session=None)
        self.assertEqual(errore.exception.status_code, 429)

    def test_i_tentativi_vecchi_non_contano(self) -> None:
        auth._tentativi["mario"] = [-auth.TENTATIVI_FINESTRA] * auth.TENTATIVI_MAX
        self.assertEqual(auth._tentativi_recenti("mario"), [])


class ReportExcelTests(unittest.TestCase):
    def test_una_descrizione_con_uguale_resta_testo(self) -> None:
        report = {"period": "2026-01", "income": 0, "expenses": 0, "savings": 0, "net_worth": 0,
                  "transactions": [{"date": "2026-01-02", "type": "Expenses", "category": "Other",
                                    "description": "=HYPERLINK(\"http://x\")", "account": "Banca",
                                    "destination": None, "signed_amount": -1}]}
        cella = load_workbook(BytesIO(excel_report(report).getvalue()))["Movimenti"]["D2"]
        self.assertEqual(cella.data_type, "s")


if __name__ == "__main__":
    unittest.main()
