// Gli indici che si scelgono di solito come metro di paragone, col simbolo che
// li identifica nella stessa fonte delle quotazioni. Dati, non regole: se ne
// aggiunge uno senza toccare il codice.
//
// I simboli sono stati controllati uno per uno sulla fonte vera, e non e' una
// formalita': un simbolo sbagliato non da' errore, da' un confronto con
// un'altra cosa - o con niente - e il grafico sembra comunque una risposta. I
// nomi sono quelli con cui l'indice si chiama dappertutto, quindi non si
// traducono.
export type Benchmark = { symbol: string; name: string };

export const BENCHMARKS: readonly Benchmark[] = [
  { symbol: '^GSPC', name: 'S&P 500' },
  { symbol: '^NDX', name: 'Nasdaq 100' },
  { symbol: '^STOXX50E', name: 'EURO STOXX 50' },
  { symbol: '^GDAXI', name: 'DAX' },
  { symbol: '^FCHI', name: 'CAC 40' },
  { symbol: 'FTSEMIB.MI', name: 'FTSE MIB' },
  { symbol: '^FTSE', name: 'FTSE 100' },
  { symbol: '^SSMI', name: 'SMI' },
  { symbol: '^IBEX', name: 'IBEX 35' },
  { symbol: '^N225', name: 'Nikkei 225' },
  // Gli ETF, per chi si confronta con quello che puo' comprare davvero: la
  // quotazione in euro e' la stessa cosa che vede nel portafoglio.
  { symbol: 'SWDA.MI', name: 'MSCI World (ETF)' },
  { symbol: 'VWCE.DE', name: 'FTSE All-World (ETF)' },
  { symbol: 'EIMI.L', name: 'MSCI Emerging Markets (ETF)' },
  { symbol: 'AGGH.MI', name: 'Global Aggregate Bond (ETF)' },
];
