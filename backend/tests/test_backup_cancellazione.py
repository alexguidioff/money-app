"""Cancellare una copia di sicurezza, e solo quelle.

Il nome del file arriva dall'URL. Senza il controllo sul percorso, un `..`
porterebbe a cancellare file fuori dalla cartella dei backup: e' l'unico punto
dell'app dove un parametro di richiesta diventa una `unlink()`.

La cancellazione serve perche' la conservazione automatica sfoltisce soltanto
`auto` e `pre-import`: ogni altro dump resta per sempre, e prima non c'era modo
di togliere i ventisette residui delle migrazioni senza entrare nel container.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from app import backup as B


class CancellazioneBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cartella = TemporaryDirectory()
        self.dir = Path(self.cartella.name)
        self.patch = mock.patch.object(B, "BACKUPS_DIR", self.dir)
        self.patch.start()
        (self.dir / "auto-20260912T0000.dump").write_bytes(b"finto")
        self.fuori = self.dir.parent / "da-non-toccare.txt"
        self.fuori.write_text("resta qui")

    def tearDown(self) -> None:
        self.patch.stop()
        self.fuori.unlink(missing_ok=True)
        self.cartella.cleanup()

    def test_cancella_il_dump_richiesto(self) -> None:
        esito = B.delete_backup("auto-20260912T0000.dump")
        self.assertEqual(esito["deleted"], "auto-20260912T0000.dump")
        self.assertFalse((self.dir / "auto-20260912T0000.dump").exists())

    def test_un_nome_con_percorso_viene_rifiutato(self) -> None:
        for cattivo in ("../da-non-toccare.txt", "/etc/passwd", "sotto/auto.dump"):
            with self.assertRaises(ValueError, msg=cattivo):
                B.delete_backup(cattivo)
        self.assertTrue(self.fuori.exists(), "un file fuori dalla cartella e' stato toccato")

    def test_un_dump_inesistente_non_e_un_errore_generico(self) -> None:
        with self.assertRaises(FileNotFoundError):
            B.delete_backup("mai-esistito-20200101T0000.dump")

    def test_il_ripristino_usa_lo_stesso_controllo(self) -> None:
        # La validazione vive in un posto solo: se qualcuno la allenta per la
        # cancellazione, deve cadere anche qui.
        with self.assertRaises(ValueError):
            B.restore_backup("../da-non-toccare.txt")
