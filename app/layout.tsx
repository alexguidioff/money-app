import type { Metadata, Viewport } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import './globals.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  title: 'Money — Le tue finanze',
  description: 'Gestione personale di conti, budget e movimenti.',
  // Il file esisteva gia' in public/ ma non lo dichiarava nessuno: il browser
  // cerca /favicon.ico e non lo trovava, quindi la scheda restava senza icona.
  // `apple` serve a iOS, che il manifest non lo legge per l'icona in schermata
  // iniziale: senza, sul telefono resterebbe uno screenshot della pagina.
  icons: { icon: '/favicon.svg', apple: '/apple-touch-icon.png' },
};

// La barra di stato, quando l'app e' aperta a schermo intero dal telefono.
export const viewport: Viewport = {
  themeColor: '#51461a',
  // `contain` tiene la pagina dentro le zone sicure del telefono. `cover`
  // farebbe arrivare lo sfondo fino ai bordi, ma con esso la barra in alto -
  // che e' fissa - finirebbe sotto il notch, e il pulsante del menu sparirebbe
  // dietro l'orologio: tenerlo fuori costa una fascia di sfondo, non un
  // comando che non si trova piu'.
  viewportFit: 'contain',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="it">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
