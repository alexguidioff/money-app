# Money

[Italiano](README.md) · [English](README.en.md) · **Deutsch** · [Español](README.es.md) · [Français](README.fr.md)

Eine App für persönliche Finanzen, die **bei dir zu Hause** läuft: deine
Buchungen, Konten und Investitionen bleiben in deiner eigenen Datenbank, auf
einem Rechner, den du kontrollierst. Kein externer Dienst sieht deine Zahlen.

Sie ist als Ersatz für eine über Jahre gewachsene Tabellenkalkulation
entstanden und hat deren Grundsatz übernommen: **die Zahlen müssen stimmen**.
Jede Summe wird aus den Buchungen berechnet, nirgendwo abgeschrieben, und die
berechneten Salden lassen sich mit den angegebenen vergleichen – so merkst du
sofort, wenn etwas nicht aufgeht.

## Was sie kann

- **Buchungen** — Einnahmen, Ausgaben, Umbuchungen zwischen Konten,
  Daueraufträge. Import von Kontoauszügen als PDF oder CSV, mit Vorschau vor dem
  Speichern: PDFs werden auch dann gelesen, wenn die Bank keine Tabelle zeichnet,
  sondern den Text nur in Spalten ausrichtet.
- **Konten** — wie eine Bilanz, aufgeteilt in Banken, Vermögenswerte,
  Verbindlichkeiten und Investitionen, mit dem berechneten Saldo neben dem
  erwarteten.
- **Budget** — Planung nach Kategorie und Monat, Vergleich mit den Ausgaben,
  Jahresverlauf.
- **Ziele** — wie viel fehlt, in welchem Tempo, bis wann.
- **Schulden** — Darlehen und Kreditlinien mit dem theoretischen Tilgungsplan
  neben dem, was tatsächlich ausgezahlt, belastet und zurückgezahlt wurde.
- **Vermögen** — die monatliche Reihe des Nettovermögens, auch in anderen
  Währungen zum Kurs des jeweiligen Monats statt zum heutigen.
- **Investitionen** — Portfolio zu Marktpreisen, die über die Ticker geladen
  werden, Aufteilung nach Sektor und nach zugrunde liegendem Titel, Verlauf
  jedes Instruments mit deinen eigenen Käufen und Verkäufen darauf.
- **Ruhestand und FIRE** — wie viel Kapital du brauchst, in welchem Jahr du es
  erreichst und was es verschiebt: Sparquote, künftige Renten, Ausgaben, die
  sich im Ruhestand ändern, mit Steuerhinweisen für zehn europäische Länder.
- **Notizen** — freie Notizen, um festzuhalten, warum du etwas entschieden hast.
- **Gemeinsam** — wer möchte, teilt seine *Summen* mit den anderen Konten
  derselben Installation. Nie Buchungen, nie Kategorien, nie Konten.
- **Hinweise** — überschrittene Budgets, veraltete Kurse, ausbleibende Backups.
  Einmal geschlossen, kommen sie nicht wieder.

Oben auf jeder Seite erklärt ein **„?“**, wofür die Seite da ist und was sie
braucht, um sich mit Zahlen zu füllen.

Die Oberfläche gibt es auf Italienisch, Englisch, Deutsch, Spanisch und
Französisch.

## Loslegen

Du brauchst nur Docker.

```bash
git clone https://github.com/alexguidioff/money-app.git money
cd money
./setup.sh                    # legt die .env mit zwei zufälligen Passwörtern an
docker compose up -d --build
```

Dann öffne <http://localhost:3010> und **lege das erste Konto an** auf dem
Bildschirm, der erscheint. Die App startet leer: Es sind keine Daten von
anderen darin.

Weitere Personen fügst du auf demselben Anmeldebildschirm hinzu: Jede hat ihre
eigenen, getrennten Buchungen. Das Passwort ist optional – solange niemand eines
festlegt, öffnet sich die App, ohne etwas zu fragen.

## Die zwei Datenbank-Passwörter

Es sind nicht die, mit denen du dich in der App anmeldest: Es sind die
Schlüssel, mit denen die App mit der Datenbank spricht, und **niemand muss sie
je eintippen**. `setup.sh` erzeugt zwei zufällige und schreibt sie in die Datei
`.env`, die Docker beim Start liest.

Ein Mensch braucht sie nur, um die Datenbank von Hand zu öffnen oder ein Backup
außerhalb der App wiederherzustellen: In beiden Fällen stehen sie in `.env`.

Geht die `.env` verloren, während die Daten noch da sind, ist nichts verloren:
Im Container akzeptiert die Datenbank lokale Verbindungen ohne Passwort.

```bash
./setup.sh                                  # eine neue .env mit neuen Passwörtern
docker compose exec db psql -U money -d money \
  -c "alter role money password '<POSTGRES_PASSWORD aus der neuen .env>'"
docker compose up -d                        # das andere schreibt die App selbst neu
```

## Eine Person löschen

Unter **Einstellungen → Konto**, und **nur das eigene Konto**: Es gibt keinen
Administrator, der die Zahlen anderer löschen kann. Zur Bestätigung tippst du
deinen eigenen Namen ein, und vor dem Löschen exportiert die App die Daten im
Austauschformat und sichert die Datenbank – die Kopie bleibt auch neben den
Backups auf dem Server.

## Wie die Daten zwischen Personen getrennt sind

Nicht durch disziplinierte Abfragen, sondern **durch die Datenbank**. Jede
Tabelle mit persönlichen Daten hat eine PostgreSQL-Zeilenrichtlinie, die an den
Benutzer der Anfrage gebunden ist, und die App verbindet sich mit einer Rolle
ohne Sonderrechte: Eine Abfrage, die den Filter vergisst, sieht keine fremden
Daten – sie findet schlicht nichts.

Das ist eine bewusste Entscheidung. Das Risiko einer solchen App ist nicht, dass
sie sichtbar kaputtgeht, sondern dass sie still die Zahlen zweier Menschen
vermischt.

## Wo die Daten liegen

In einem Docker-Volume mit PostgreSQL. Von dort gehen sie nicht weg:

- **Automatische Backups** jeden Tag, dazu eines vor jedem Import, der die Daten
  ersetzt. Wiederherstellen kannst du sie in der Oberfläche, unter Berichte.
- **Vollständiger Export** im „Austauschformat“: ein Blatt pro Entität,
  ISO-Datumsangaben, keine Formeln. Damit nimmst du alles mit und spielst es in
  eine andere Installation ein – ein Test prüft, dass Export und Import genau
  das zurückgeben, was sie genommen haben.

Ins Internet gehen nur die **Kurse** der Instrumente, die du mit einem Ticker
eingerichtet hast, und die **Wechselkurse** der Währungen, die du auswählst.

## Auf einem NAS betreiben

Die App ist dafür gedacht, auf einem ständig laufenden Rechner zu landen, den
die Geräte zu Hause erreichen.

1. `MONEY_BIND_ADDRESS=0.0.0.0` in der `.env`
2. Setze ein privates Netz davor – [Tailscale](https://tailscale.com) ist der
   einfachste Weg: verschlüsselter Verkehr, keine offenen Ports am Router, und
   mit `tailscale serve` auch ein echtes HTTPS-Zertifikat
3. `MONEY_APP_ORIGIN=https://...` mit der Adresse, unter der du die App öffnest.
   Sie wird zweimal gebraucht: Das Sitzungscookie erkennt selbst, dass es hinter
   HTTPS läuft, und die API nimmt nur Anfragen von `localhost`, `127.0.0.1` und
   dieser Adresse an – eine andere Website im selben Browser kann deine Daten
   weder lesen noch ändern
4. Baue die Images **auf dem NAS** (`docker compose up -d --build`), kopiere sie
   nicht: Die Architektur kann eine andere sein als die deines Computers

Ohne privates Netz davor solltest du sie nicht erreichbar machen: Passwörter und
Buchungen gingen unverschlüsselt durchs lokale Netz. Gib jedem Konto ein
Passwort: Nach zehn Fehlversuchen innerhalb einer Viertelstunde wird die
Anmeldung für diesen Namen eine Weile gesperrt.

## Wie sie gebaut ist

- **API** — FastAPI und SQLAlchemy auf PostgreSQL 17
- **Oberfläche** — React (mit vinext auf Vite), in fünf Sprachen
- **Gateway** — nginx vor beidem, damit der Browser mit nur einem Ursprung spricht

```bash
pnpm install
docker compose exec api python -m pytest tests/ -q   # Backend-Tests
pnpm test                                            # Unit- und Vertragstests des Frontends
pnpm test:e2e                                        # End-to-End mit Playwright auf einem Wegwerf-Stack
scripts/gate.sh                                      # alles nacheinander, veröffentlicht nur, wenn alles grün ist
```

Die Tests decken die Rechenlogik ab, das Einlesen von Kontoauszügen, den Weg
Export–Import und die Summen, die früher still kaputtgegangen sind: eine
Vorlage für Daueraufträge, die als echte Ausgabe zählte, investiertes Kapital,
bei dem verkaufte Positionen vergessen wurden. Die **Verträge** vergleichen die
echten Antworten der API mit den Typen, die die Oberfläche liest, und die
Anfragen der Oberfläche mit dem, was die API annimmt: Ein nur auf einer Seite
umbenanntes Feld stoppt das Gate.

Die End-to-End-Tests (`pnpm test:e2e`, vorher `pnpm exec playwright install
chromium`) laufen in einem eigenen Compose-Projekt, Port 3011, Datenbank im
Arbeitsspeicher: Sie berühren nie die Installation, die du benutzt. Sie brauchen
die Images `money-app-api` und `money-app-web`, die `docker compose build`
erzeugt.

## Lizenz

MIT.
