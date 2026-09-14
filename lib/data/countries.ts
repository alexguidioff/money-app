// Dati, non regole: per ogni paese l'eta' pensionabile ordinaria e il link al
// simulatore ufficiale. Niente formule, niente coefficienti: quei numeri
// cambiano e invecchiano male, e l'utente li prende dal suo ente nazionale.
//
// Le eta' sono l'ordinary retirement age in vigore al 2026. Se salgono -
// come succede in diversi paesi agganciati all'aspettativa di vita - e'
// l'utente a spostare il cursore, non noi a riscrivere il dato.

export type Country = {
  code: string;             // ISO 3166-1 alpha-2
  nameIt: string;
  nameEn: string;
  nameDe: string;
  nameEs: string;
  nameFr: string;
  retirementAge: number;    // ordinary, anni
  simulatorUrl: string;     // simulatore ufficiale
};

// I nomi dei paesi sono brevi: l'utente vede la propria lingua. Le etichette
// tradotte servono quando il menu mostra piu' paesi vicini.
export const COUNTRIES: readonly Country[] = [
  { code: 'IT', nameIt: 'Italia', nameEn: 'Italy', nameDe: 'Italien', nameEs: 'Italia', nameFr: 'Italie',
    retirementAge: 67, simulatorUrl: 'https://www.inps.it/prestazioni-servizi/scopri-il-pacchetto-previdenziale-per-i-lavoratori-dipendenti-e-autonomi' },
  { code: 'CH', nameIt: 'Svizzera', nameEn: 'Switzerland', nameDe: 'Schweiz', nameEs: 'Suiza', nameFr: 'Suisse',
    retirementAge: 65, simulatorUrl: 'https://www.ahv-iv.ch/p/itr/it/startseite/leistungen/renten-berechnen.html' },
  { code: 'DE', nameIt: 'Germania', nameEn: 'Germany', nameDe: 'Deutschland', nameEs: 'Alemania', nameFr: 'Allemagne',
    retirementAge: 67, simulatorUrl: 'https://www.deutsche-rentenversicherung.de/DRV/DE/Online-Dienste/rentenantrag/online_rentenantrag_node.html' },
  { code: 'FR', nameIt: 'Francia', nameEn: 'France', nameDe: 'Frankreich', nameEs: 'Francia', nameFr: 'France',
    retirementAge: 64, simulatorUrl: 'https://www.info-retraite.fr/portail-info/sites/default/files/2024-01/INFO-HISTO-RETRAITE-2024-PLAQUETTE-INTERACTIF_0.pdf' },
  { code: 'ES', nameIt: 'Spagna', nameEn: 'Spain', nameDe: 'Spanien', nameEs: 'España', nameFr: 'Espagne',
    retirementAge: 66, simulatorUrl: 'https://sede.seg-social.gob.es/wps/portal/sede/sede/Trabajadores/PreparaTuJubilacion' },
  { code: 'NL', nameIt: 'Paesi Bassi', nameEn: 'Netherlands', nameDe: 'Niederlande', nameEs: 'Países Bajos', nameFr: 'Pays-Bas',
    retirementAge: 67, simulatorUrl: 'https://www.mijnpensioenoverzicht.nl/' },
  { code: 'AT', nameIt: 'Austria', nameEn: 'Austria', nameDe: 'Österreich', nameEs: 'Austria', nameFr: 'Autriche',
    retirementAge: 65, simulatorUrl: 'https://www.pensionsversicherung.at/cdscontent/?contentid=10007.852667' },
  { code: 'BE', nameIt: 'Belgio', nameEn: 'Belgium', nameDe: 'Belgien', nameEs: 'Bélgica', nameFr: 'Belgique',
    retirementAge: 67, simulatorUrl: 'https://www.sfpd.fgov.be/nl/sd-salaire-digitaal' },
  { code: 'UK', nameIt: 'Regno Unito', nameEn: 'United Kingdom', nameDe: 'Vereinigtes Königreich', nameEs: 'Reino Unido', nameFr: 'Royaume-Uni',
    retirementAge: 67, simulatorUrl: 'https://www.gov.uk/check-state-pension' },
  { code: 'PT', nameIt: 'Portogallo', nameEn: 'Portugal', nameDe: 'Portugal', nameEs: 'Portugal', nameFr: 'Portugal',
    retirementAge: 66, simulatorUrl: 'https://www.seg-social.pt/marco-legislativo' },
];

export function countryByCode(code: string): Country | undefined {
  return COUNTRIES.find((c) => c.code === code);
}

// Le note fiscali per paese: niente calcoli, solo avvisi. L'aliquota sui
// prelievi la dichiara l'utente nel modulo profilo; qui si segnalano i
// regimi che hanno trappole note (plusvalenze, patrimoniali, forfait).
export type TaxNote = {
  country: string;            // ISO code
  language: 'it' | 'en' | 'de' | 'es' | 'fr';
  body: string;
};

export const TAX_NOTES: readonly TaxNote[] = [
  { country: 'IT', language: 'it',
    body: 'In Italia i rendimenti dei fondi pensione e del TFR sono tassati al 15%, aliquota che scende fino al 9% dopo 35 anni di partecipazione. Le plusvalenze finanziarie fuori dal perimetro previdenziale sono al 26%. Non esiste un\'imposta patrimoniale generale.' },
  { country: 'IT', language: 'en',
    body: 'In Italy, returns on pension funds and severance pay (TFR) are taxed at 15%, a rate that falls to 9% after 35 years of membership. Financial capital gains outside the pension perimeter are taxed at 26%. There is no general wealth tax.' },
  { country: 'IT', language: 'de',
    body: 'In Italien werden Erträge aus Pensionsfonds und der Abfertigung (TFR) mit 15% besteuert; nach 35 Beitragsjahren sinkt der Satz auf 9%. Kapitalerträge außerhalb der Altersvorsorge werden mit 26% besteuert. Eine allgemeine Vermögensteuer gibt es nicht.' },
  { country: 'IT', language: 'es',
    body: 'En Italia, los rendimientos de los fondos de pensiones y del TFR tributan al 15%, tipo que baja hasta el 9% tras 35 años de participación. Las plusvalías financieras fuera del perímetro de previsión tributan al 26%. No existe un impuesto general sobre el patrimonio.' },
  { country: 'IT', language: 'fr',
    body: 'En Italie, les rendements des fonds de pension et du TFR sont taxés à 15%, taux qui descend à 9% après 35 ans de participation. Les plus-values financières hors périmètre de retraite sont taxées à 26%. Il n\'existe pas d\'impôt général sur la fortune.' },
  { country: 'CH', language: 'it',
    body: 'In Svizzera il 2° pilastro (LPP) e il pilastro 3a sono esenti da imposte fino al prelievo. Alla liquidazione in capitale si applica un\'aliquota separata, più bassa di quella ordinaria e progressiva con l\'importo. Il prelievo in capitale del 2° pilastro è ammesso solo in casi specifici.' },
  { country: 'CH', language: 'en',
    body: 'In Switzerland, the 2nd pillar (BVG/LPP) and pillar 3a are tax-exempt until withdrawal. A lump-sum payout is taxed at a separate rate, lower than the ordinary one and rising with the amount. Taking the 2nd pillar as capital is allowed only in specific cases.' },
  { country: 'CH', language: 'de',
    body: 'In der Schweiz sind die 2. Säule (BVG) und die Säule 3a bis zum Bezug steuerfrei. Der Kapitalbezug wird zu einem gesonderten, gegenüber dem ordentlichen Tarif tieferen und mit der Höhe steigenden Satz besteuert. Der Kapitalbezug der 2. Säule ist nur in bestimmten Fällen zulässig.' },
  { country: 'CH', language: 'es',
    body: 'En Suiza, el 2.º pilar (LPP) y el pilar 3a están exentos de impuestos hasta el rescate. El cobro en capital tributa a un tipo separado, más bajo que el ordinario y creciente con el importe. El rescate en capital del 2.º pilar solo se permite en casos concretos.' },
  { country: 'CH', language: 'fr',
    body: 'En Suisse, le 2e pilier (LPP) et le pilier 3a sont exonérés d\'impôt jusqu\'au retrait. Le versement en capital est taxé à un taux distinct, plus bas que le taux ordinaire et croissant avec le montant. Le retrait en capital du 2e pilier n\'est autorisé que dans des cas précis.' },
  { country: 'DE', language: 'it',
    body: 'In Germania la Riester-Rente si costituisce con versamenti già tassati ed è tassata all\'erogazione; la Basis-Rente (Rürup) è deducibile all\'ingresso e integralmente tassata all\'uscita. Le plusvalenze su titoli sono al 25% (Abgeltungsteuer) più il Solidaritätszuschlag.' },
  { country: 'DE', language: 'en',
    body: 'In Germany, the Riester pension is funded with after-tax money and taxed on payout; the Basis-Rente (Rürup) is deductible on the way in and fully taxed on the way out. Securities capital gains are taxed at 25% (Abgeltungsteuer) plus the solidarity surcharge.' },
  { country: 'DE', language: 'de',
    body: 'In Deutschland wird die Riester-Rente aus versteuertem Einkommen aufgebaut und in der Auszahlungsphase besteuert; die Basis-Rente (Rürup) ist beim Einzahlen absetzbar und in der Auszahlung voll steuerpflichtig. Kursgewinne unterliegen der Abgeltungsteuer von 25% zuzüglich Solidaritätszuschlag.' },
  { country: 'DE', language: 'es',
    body: 'En Alemania, la Riester-Rente se nutre de aportaciones ya tributadas y se grava en la prestación; la Basis-Rente (Rürup) es deducible a la entrada y tributa íntegramente a la salida. Las plusvalías en valores tributan al 25% (Abgeltungsteuer) más el recargo de solidaridad.' },
  { country: 'DE', language: 'fr',
    body: 'En Allemagne, la Riester-Rente est alimentée par des versements déjà imposés et taxée au versement de la rente ; la Basis-Rente (Rürup) est déductible à l\'entrée et pleinement imposée à la sortie. Les plus-values sur titres sont taxées à 25% (Abgeltungsteuer) plus la contribution de solidarité.' },
  { country: 'FR', language: 'it',
    body: 'In Francia l\'assurance-vie dopo 8 anni gode di un regime agevolato al 7,5% o 12,8%. Il PER (Plan d\'Épargne Retraite) è deducibile all\'ingresso e tassato all\'uscita, salvo casi specifici. I prelievi prima dell\'età pensionabile sono possibili solo in casi tassativi.' },
  { country: 'FR', language: 'en',
    body: 'In France, an assurance-vie held for over 8 years benefits from a reduced rate of 7.5% or 12.8%. The PER (retirement savings plan) is deductible on the way in and taxed on the way out, with a few exceptions. Withdrawals before retirement age are allowed only in listed cases.' },
  { country: 'FR', language: 'de',
    body: 'In Frankreich profitiert die assurance-vie nach acht Jahren von einem ermäßigten Satz von 7,5% oder 12,8%. Der PER (Altersvorsorgeplan) ist beim Einzahlen absetzbar und bei der Auszahlung steuerpflichtig, von Sonderfällen abgesehen. Entnahmen vor dem Rentenalter sind nur in abschließend geregelten Fällen möglich.' },
  { country: 'FR', language: 'es',
    body: 'En Francia, el assurance-vie con más de 8 años disfruta de un tipo reducido del 7,5% o del 12,8%. El PER (plan de ahorro para la jubilación) es deducible a la entrada y tributa a la salida, salvo casos concretos. Los rescates antes de la edad de jubilación solo se admiten en supuestos tasados.' },
  { country: 'FR', language: 'fr',
    body: 'En France, l\'assurance-vie de plus de 8 ans bénéficie d\'un taux réduit de 7,5% ou 12,8%. Le PER (Plan d\'Épargne Retraite) est déductible à l\'entrée et imposé à la sortie, sauf cas particuliers. Les retraits avant l\'âge de la retraite ne sont possibles que dans des cas limitativement énumérés.' },
  { country: 'ES', language: 'it',
    body: 'In Spagna i planes de pensiones sono tassati come reddito da lavoro al momento dell\'erogazione (rendimiento del trabajo). Le plusvalenze finanziarie sono al 19-28% per scaglioni. Non c\'è un\'imposta patrimoniale nazionale uniforme, ma diverse comunità autonome la applicano.' },
  { country: 'ES', language: 'en',
    body: 'In Spain, pension plans are taxed as employment income when paid out (rendimiento del trabajo). Financial capital gains are taxed at 19-28% in brackets. There is no uniform national wealth tax, but several autonomous communities levy one.' },
  { country: 'ES', language: 'de',
    body: 'In Spanien werden Pensionspläne bei der Auszahlung wie Arbeitseinkommen besteuert (rendimiento del trabajo). Kapitalgewinne unterliegen einem Stufentarif von 19-28%. Eine einheitliche nationale Vermögensteuer gibt es nicht, mehrere autonome Regionen erheben jedoch eine.' },
  { country: 'ES', language: 'es',
    body: 'En España, los planes de pensiones tributan como rendimiento del trabajo en el momento del rescate. Las plusvalías financieras tributan al 19-28% por tramos. No hay un impuesto sobre el patrimonio estatal uniforme, pero varias comunidades autónomas lo aplican.' },
  { country: 'ES', language: 'fr',
    body: 'En Espagne, les plans de pension sont imposés comme des revenus du travail au moment du versement (rendimiento del trabajo). Les plus-values financières sont taxées de 19 à 28% par tranches. Il n\'existe pas d\'impôt sur la fortune national uniforme, mais plusieurs communautés autonomes en appliquent un.' },
  { country: 'NL', language: 'it',
    body: 'Nei Paesi Bassi vale il regime Box 3: il patrimonio è tassato su un rendimento forfettario, non su quello realmente ottenuto, con percentuali diverse per fascia di attività. È un caso particolare in Europa e va tenuto presente da chi investe a lungo termine.' },
  { country: 'NL', language: 'en',
    body: 'In the Netherlands the Box 3 regime applies: wealth is taxed on a deemed return rather than the return actually earned, with different rates per asset class. It is unusual in Europe and matters for anyone investing over the long run.' },
  { country: 'NL', language: 'de',
    body: 'In den Niederlanden gilt das Box-3-Regime: Vermögen wird auf eine fiktive Rendite besteuert, nicht auf die tatsächlich erzielte, mit unterschiedlichen Sätzen je Vermögensklasse. Das ist in Europa ein Sonderfall und für langfristige Anleger relevant.' },
  { country: 'NL', language: 'es',
    body: 'En los Países Bajos rige el régimen Box 3: el patrimonio tributa sobre un rendimiento presunto, no sobre el realmente obtenido, con porcentajes distintos por tipo de activo. Es un caso singular en Europa y conviene tenerlo en cuenta al invertir a largo plazo.' },
  { country: 'NL', language: 'fr',
    body: 'Aux Pays-Bas s\'applique le régime Box 3 : le patrimoine est imposé sur un rendement forfaitaire et non sur le rendement réellement obtenu, avec des taux différents par catégorie d\'actifs. C\'est un cas particulier en Europe, à prendre en compte pour un investissement de long terme.' },
  { country: 'UK', language: 'it',
    body: 'Nel Regno Unito i SIPP e le personal pension godono di sgravio sui contributi ed esenzione sui rendimenti, con tassazione all\'uscita; una quota del capitale (di norma il 25%) è esente. Le plusvalenze fuori dal perimetro previdenziale hanno un regime proprio, con una franchigia annua.' },
  { country: 'UK', language: 'en',
    body: 'In the UK, SIPPs and personal pensions get tax relief on contributions and tax-free growth, and are taxed on withdrawal; part of the pot (usually 25%) is tax-free. Capital gains outside the pension wrapper have their own rates and an annual allowance.' },
  { country: 'UK', language: 'de',
    body: 'Im Vereinigten Königreich erhalten SIPPs und private Renten eine Steuererleichterung auf Beiträge, wachsen steuerfrei und werden bei der Entnahme besteuert; ein Teil des Kapitals (in der Regel 25%) bleibt steuerfrei. Kursgewinne außerhalb der Altersvorsorge haben eigene Sätze und einen Jahresfreibetrag.' },
  { country: 'UK', language: 'es',
    body: 'En el Reino Unido, los SIPP y los planes personales tienen desgravación en las aportaciones y crecimiento exento, y tributan en el rescate; una parte del capital (por lo general el 25%) queda exenta. Las plusvalías fuera del perímetro de previsión tienen tipos propios y un mínimo exento anual.' },
  { country: 'UK', language: 'fr',
    body: 'Au Royaume-Uni, les SIPP et les retraites personnelles bénéficient d\'un allègement sur les cotisations et d\'une croissance exonérée, et sont imposés à la sortie ; une part du capital (en général 25%) est exonérée. Les plus-values hors enveloppe retraite ont leurs propres taux et un abattement annuel.' },
  { country: 'AT', language: 'it',
    body: 'In Austria la previdenza aziendale (Betriebliche Vorsorge) è tassata all\'erogazione, con un trattamento agevolato per la rendita rispetto al capitale. Le plusvalenze su titoli sono al 27,5% (Kapitalertragsteuer).' },
  { country: 'AT', language: 'en',
    body: 'In Austria, occupational provision (Betriebliche Vorsorge) is taxed on payout, with the annuity treated more favourably than a lump sum. Securities capital gains are taxed at 27.5% (Kapitalertragsteuer).' },
  { country: 'AT', language: 'de',
    body: 'In Österreich wird die betriebliche Vorsorge bei der Auszahlung besteuert, wobei die Rente günstiger behandelt wird als der Kapitalbezug. Kursgewinne unterliegen der Kapitalertragsteuer von 27,5%.' },
  { country: 'AT', language: 'es',
    body: 'En Austria, la previsión empresarial (Betriebliche Vorsorge) tributa en la prestación, con un trato más favorable para la renta que para el capital. Las plusvalías en valores tributan al 27,5% (Kapitalertragsteuer).' },
  { country: 'AT', language: 'fr',
    body: 'En Autriche, la prévoyance professionnelle (Betriebliche Vorsorge) est imposée au versement, la rente étant traitée plus favorablement que le capital. Les plus-values sur titres sont taxées à 27,5% (Kapitalertragsteuer).' },
  { country: 'BE', language: 'it',
    body: 'In Belgio la pensione legale è tassata come reddito, con un\'imposizione fortemente progressiva. I piani complementari per autonomi (VAPZ/POZ) danno un beneficio fiscale sui versamenti e sono tassati all\'uscita. Non c\'è un\'imposta generale sul patrimonio.' },
  { country: 'BE', language: 'en',
    body: 'In Belgium, the state pension is taxed as income under a steeply progressive scale. Supplementary plans for the self-employed (VAPZ/POZ) give tax relief on contributions and are taxed on payout. There is no general wealth tax.' },
  { country: 'BE', language: 'de',
    body: 'In Belgien wird die gesetzliche Rente als Einkommen nach einem stark progressiven Tarif besteuert. Zusatzvorsorgepläne für Selbstständige (VAPZ/POZ) bringen eine Steuerentlastung auf die Beiträge und werden bei der Auszahlung besteuert. Eine allgemeine Vermögensteuer gibt es nicht.' },
  { country: 'BE', language: 'es',
    body: 'En Bélgica, la pensión pública tributa como renta con una escala muy progresiva. Los planes complementarios para autónomos (VAPZ/POZ) dan ventaja fiscal en las aportaciones y tributan en la prestación. No existe un impuesto general sobre el patrimonio.' },
  { country: 'BE', language: 'fr',
    body: 'En Belgique, la pension légale est imposée comme un revenu selon un barème fortement progressif. Les plans complémentaires pour indépendants (VAPZ/POZ) donnent un avantage fiscal sur les versements et sont imposés à la sortie. Il n\'existe pas d\'impôt général sur la fortune.' },
  { country: 'PT', language: 'it',
    body: 'In Portogallo i PPR (piani di risparmio previdenziale) danno una detrazione sui versamenti e una tassazione ridotta all\'uscita se il piano è mantenuto abbastanza a lungo e il riscatto rispetta le condizioni previste. Le plusvalenze finanziarie sono al 28%.' },
  { country: 'PT', language: 'en',
    body: 'In Portugal, PPR retirement savings plans give tax relief on contributions and a reduced rate on withdrawal, provided the plan is held long enough and the payout meets the stated conditions. Financial capital gains are taxed at 28%.' },
  { country: 'PT', language: 'de',
    body: 'In Portugal gewähren PPR-Altersvorsorgepläne eine Steuerermäßigung auf die Beiträge und einen reduzierten Satz bei der Entnahme, sofern der Plan lange genug gehalten wird und die Auszahlung die Bedingungen erfüllt. Kapitalgewinne werden mit 28% besteuert.' },
  { country: 'PT', language: 'es',
    body: 'En Portugal, los PPR (planes de ahorro para la jubilación) dan desgravación en las aportaciones y una tributación reducida en el rescate, siempre que el plan se mantenga el tiempo suficiente y el cobro cumpla las condiciones previstas. Las plusvalías financieras tributan al 28%.' },
  { country: 'PT', language: 'fr',
    body: 'Au Portugal, les PPR (plans d\'épargne retraite) donnent un allègement sur les versements et une imposition réduite à la sortie, à condition que le plan soit conservé assez longtemps et que le rachat respecte les conditions prévues. Les plus-values financières sont taxées à 28%.' },
];
