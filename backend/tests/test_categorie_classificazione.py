"""Bisogno o piacere: un attributo della categoria, che si eredita dal padre.

La classificazione ha cambiato casa due volte, e questi test la fissano dove
sta adesso: una colonna sulla categoria, un solo gradino di ereditarieta', e
niente da inventare quando nessuno dei due ha parlato.

I nomi sono inventati e tondi: questo repository e' pubblico e i valori veri di
chi usa l'app non ci entrano.
"""
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.categorie import (CategoryGroupPayload, classifica_categoria, essenziale_di_categoria,
                           gruppo_di_categoria)
from app.database import Base
from app.models import Category


class ClassificazioneTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def crea(self, nome, padre=None, essenziale=None, scope="expense") -> Category:
        riga = Category(parent_id=padre.id if padre else None, name=nome, essenziale=essenziale, scope=scope)
        self.session.add(riga)
        self.session.flush()
        return riga

    def classifica(self, riga, gruppo):
        return classifica_categoria(CategoryGroupPayload(categoryId=riga.id, category_group=gruppo), self.session)

    def test_1_una_categoria_che_dichiara_vale_quello(self):
        """Quello che la categoria dice di se' non lo decide nessun altro."""
        casa = self.crea("Casa", essenziale="needs")
        self.assertEqual(essenziale_di_categoria(self.session)[casa.id], "needs")
        self.assertEqual(gruppo_di_categoria(self.session)[casa.id], "Needs")

    def test_2_una_che_non_dichiara_eredita_dal_padre(self):
        """Dire "Casa e' un bisogno" una volta basta per tutti i suoi figli."""
        casa = self.crea("Casa", essenziale="needs")
        affitto = self.crea("Affitto", padre=casa)
        utenze = self.crea("Utenze", padre=casa)
        effettivi = essenziale_di_categoria(self.session)
        self.assertIsNone(affitto.essenziale)
        self.assertEqual({effettivi[affitto.id], effettivi[utenze.id]}, {"needs"})

    def test_3_un_figlio_che_dichiara_il_contrario_vince_sul_padre(self):
        """E' il caso per cui l'attributo esiste: dentro Casa, Arredamento no.

        Con i bisogni come padre questo non si poteva dire, e i due fratelli
        sarebbero stati per forza uguali.
        """
        casa = self.crea("Casa", essenziale="needs")
        arredamento = self.crea("Arredamento", padre=casa, essenziale="wants")
        affitto = self.crea("Affitto", padre=casa)
        effettivi = essenziale_di_categoria(self.session)
        self.assertEqual(effettivi[arredamento.id], "wants")
        self.assertEqual(effettivi[affitto.id], "needs")
        self.assertEqual(gruppo_di_categoria(self.session)[arredamento.id], "Wants")

    def test_4_nessuno_dei_due_lo_dichiara_resta_vuoto(self):
        """Non detto vuol dire non detto, non un valore di comodo.

        Un piacere messo li' per riempire sarebbe un giudizio dell'utente
        scritto da qualcun altro, e la ripartizione bisogni/piaceri conterebbe
        una cosa che nessuno ha mai detto.
        """
        trasporti = self.crea("Trasporti")
        auto = self.crea("Auto", padre=trasporti)
        effettivi = essenziale_di_categoria(self.session)
        self.assertIsNone(effettivi[trasporti.id])
        self.assertIsNone(effettivi[auto.id])
        # Nel gruppo il vuoto prende il nome che l'interfaccia conosce: e' la
        # voce "da classificare", quella che si vuole veder svuotare.
        self.assertEqual(gruppo_di_categoria(self.session)[auto.id], "Other")

    def test_5_su_una_entrata_si_rifiuta(self):
        """Il rimborso di una spesa non e' un bisogno: la domanda non si pone."""
        stipendio = self.crea("Stipendio", scope="income")
        with self.assertRaises(HTTPException) as errore:
            self.classifica(stipendio, "Needs")
        self.assertEqual(errore.exception.status_code, 422)
        self.assertEqual(errore.exception.detail, "essentialOnlyForExpenses")
        self.assertIsNone(stipendio.essenziale)

    def test_5b_ritirare_la_risposta_la_lascia_vuota(self):
        """"Other" e' il modo di dire "non lo so piu'": scrive vuoto, non una parola."""
        casa = self.crea("Casa")
        self.classifica(casa, "Needs")
        self.assertEqual(casa.essenziale, "needs")
        self.classifica(casa, "Other")
        self.assertIsNone(casa.essenziale)
