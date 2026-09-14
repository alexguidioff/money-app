from decimal import Decimal as D
from unittest import TestCase

from app.fire_engine import Flusso, piano_fire, stima_pensione_anticipata


class FireEngineTests(TestCase):
    def piano(self, **kwargs):
        values = dict(capitale=D('100000'), spese_annue=D('10000'), flussi=[],
                      eta_oggi=50, eta_ritiro=50, rendimento_reale=D('0'),
                      swr=D('.04'), aliquota_prelievo=D('0'), anno_oggi=2026,
                      eta_fine_proiezione=100)
        values.update(kwargs)
        return piano_fire(**values)

    def test_zero_rendimento_ponte_contabile(self):
        p = self.piano(flussi=[Flusso('Pensione', 'annuity', D(10000), 60)])
        self.assertEqual(D(100000), p.capitale_ponte)
        self.assertEqual(D(0), p.capitale_rabbocco)
        self.assertEqual(D(0), p.serie[10].capitale)
        self.assertIsNone(p.eta_esaurimento)
        self.assertEqual(2026, p.anno_raggiungimento)
        self.assertEqual(['accumulo', 'ponte', 'pensione'], [f.tipo for f in p.fasi])
        self.assertEqual(2036, p.fasi[1].anno_fine)

    def test_due_pensioni_in_anni_diversi(self):
        p = self.piano(flussi=[Flusso('CH', 'annuity', D(4000), 60),
                              Flusso('IT', 'annuity', D(6000), 65)])
        self.assertEqual(D(130000), p.capitale_necessario)
        self.assertEqual(D(130000), p.capitale_ponte)
        self.assertEqual(D(0), p.capitale_rabbocco)
        self.assertEqual(60, p.fasi[1].eta_fine)
        self.assertEqual(65, p.eta_regime)

    def test_capitale_una_tantum_solo_alla_sua_data(self):
        f = [Flusso('Pensione', 'annuity', D(10000), 65),
             Flusso('LPP', 'capital', D(50000), 60)]
        p = self.piano(flussi=f)
        self.assertEqual(D(100000), p.capitale_necessario)
        self.assertEqual(D(50000), p.serie[10].capitali)
        self.assertEqual(D(50000), sum(r.capitali for r in p.serie))
        self.assertEqual(D(0), self.piano(flussi=[Flusso('Gia ricevuto', 'capital', D(50000), 40)]).serie[0].capitali)

    def test_capitale_tardivo_non_paga_spese_precedenti(self):
        p = self.piano(capitale=D(0), flussi=[Flusso('Capitale', 'capital', D(1000000), 60)])
        self.assertEqual(D(100000), p.capitale_necessario)
        self.assertEqual(50, p.eta_esaurimento)
        self.assertEqual(D(10000), p.serie[0].deficit)
        self.assertGreater(p.serie[10].capitale_fine, 0)

    def test_non_indicizzata_richiede_inflazione_e_si_erode(self):
        f = [Flusso('Fissa', 'annuity', D(10000), 50, False)]
        with self.assertRaisesRegex(ValueError, 'inflazione_richiesta'):
            self.piano(flussi=f)
        p = self.piano(flussi=f, inflazione=D('.10'), eta_fine_proiezione=60)
        self.assertEqual(D('9090.91'), p.serie[1].rendite)
        self.assertGreater(p.capitale_rabbocco, 0)
        self.assertIn('rendite_non_indicizzate_escluse_dopo_regime', p.avvertenze)
        self.assertEqual(D(0), self.piano(flussi=f, inflazione=D(0)).capitale_necessario)

    def test_spese_diverse_dopo_ritiro(self):
        p = self.piano(eta_ritiro=52, spese_annue_ritiro=D(6000), versamenti_annui=D(2000))
        self.assertEqual(D(150000), p.capitale_necessario)
        self.assertEqual(D(2000), p.serie[0].versamenti)
        self.assertEqual(D(104000), p.serie[2].capitale)
        self.assertEqual(D(6000), p.serie[2].spese)
        self.assertEqual(D(0), p.serie[2].versamenti)

    def test_rendite_superiori_spese_non_creano_target_negativo(self):
        p = self.piano(flussi=[Flusso('Pensione', 'annuity', D(15000), 50)])
        self.assertEqual(D(0), p.capitale_necessario)
        self.assertEqual(D(0), p.capitale_rabbocco)
        self.assertEqual(D(105000), p.serie[0].capitale_fine)

    def test_esaurimento_non_nascosto_e_niente_debito_simulato(self):
        p = self.piano(capitale=D(15000), flussi=[Flusso('Pensione', 'annuity', D(10000), 60)])
        self.assertEqual(51, p.eta_esaurimento)
        self.assertEqual(D(5000), p.serie[1].deficit)
        self.assertTrue(all(r.capitale >= 0 for r in p.serie))
        self.assertIn('capitale_insufficiente_nella_proiezione', p.avvertenze)

    def test_aliquota_solo_sul_prelievo(self):
        p = self.piano(aliquota_prelievo=D('.2'), flussi=[Flusso('Pensione', 'annuity', D(2000), 50)])
        self.assertEqual(D(10000), p.serie[0].prelievo_lordo)
        self.assertEqual(D(250000), p.capitale_necessario)

    def test_interesse_e_versamenti_inizio_anno(self):
        p = self.piano(eta_ritiro=51, rendimento_reale=D('.1'), versamenti_annui=D(1000))
        self.assertEqual(D(111100), p.serie[0].capitale_fine)
        self.assertEqual(D(111100), p.serie[1].capitale)

    def test_traguardi_e_anno_fi_non_inventano_lean(self):
        p = self.piano(capitale=D(0), versamenti_annui=D(50000))
        self.assertEqual(2031, p.anno_raggiungimento)
        self.assertEqual(5, p.anni_mancanti)
        self.assertIsNone(p.traguardi['lean'].raggiunto)
        q = self.piano(spese_lean_annue=D(4000), anno_oggi=None)
        self.assertTrue(q.traguardi['lean'].raggiunto)
        self.assertIsNone(q.anno_raggiungimento)
        self.assertTrue(all(r.anno is None for r in q.serie))

    def test_coast_senza_versamenti(self):
        p = self.piano(eta_ritiro=60, rendimento_reale=D('.04'))
        self.assertAlmostEqual(D(250000) / D('1.04') ** 10,
                               p.traguardi['coast'].capitale_necessario, places=2)

    def test_interpolazione_pensione_endpoint_e_costo(self):
        a = stima_pensione_anticipata(D(10000), D(20000), 40, 60, 40)
        b = stima_pensione_anticipata(D(10000), D(20000), 40, 60, 50)
        c = stima_pensione_anticipata(D(10000), D(20000), 40, 60, 65)
        self.assertEqual(D(10000), a.importo_annuo)
        self.assertEqual(D(15000), b.importo_annuo)
        self.assertEqual(D(500), b.costo_annuo_per_anno_anticipo)
        self.assertEqual(D(5000), b.perdita_annua)
        self.assertEqual(D(20000), c.importo_annuo)
        p = self.piano(flussi=[Flusso('Stima', 'annuity', D(20000), 60,
                                      importo_se_smetti_oggi=D(5000))])
        self.assertEqual(D(5000), p.serie[10].rendite)
        self.assertEqual(D(15000), p.pensioni['Stima'].perdita_annua)

    def test_validazione_input_e_nessuna_mutazione(self):
        for changes in ({'capitale': D('NaN')}, {'swr': D(0)}, {'aliquota_prelievo': D(1)},
                        {'rendimento_reale': D(-1)}, {'eta_ritiro': 49},
                        {'eta_oggi': True}, {'spese_annue': 10000},
                        {'flussi': [Flusso('X', 'altro', D(1), 60)]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.piano(**changes)
        f = [Flusso('CH', 'annuity', D(10000), 65)]
        p = self.piano(flussi=f)
        self.assertEqual(p, self.piano(flussi=f))
        self.assertEqual(1, len(f))

    def test_carriera_divisa_senza_regole_nazionali(self):
        f = [Flusso('INPS', 'annuity', D(3000), 67, paese='IT'),
             Flusso('AVS', 'annuity', D(10000), 65, paese='CH'),
             Flusso('LPP', 'capital', D(100000), 65, paese='CH'),
             Flusso('3a', 'capital', D(30000), 65, paese='CH'),
             Flusso('TFR', 'capital', D(10000), 45, paese='IT')]
        p = self.piano(capitale=D(250000), spese_annue=D(19932), flussi=f,
                      eta_oggi=28, eta_ritiro=45, rendimento_reale=D('.03'),
                      versamenti_annui=D(6000))
        self.assertEqual(p.capitale_necessario, p.capitale_ponte + p.capitale_rabbocco)
        self.assertLess(p.capitale_necessario, D(19932) / D('.04'))
        self.assertEqual(D(130000), next(r.capitali for r in p.serie if r.eta == 65))
        self.assertIn('scenario_non_previsione', p.avvertenze)
