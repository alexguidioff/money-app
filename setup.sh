#!/usr/bin/env bash
#
# Prepara il file .env con due password casuali per il database.
#
# Serve una volta sola, su una macchina nuova. Le password non le deve sapere
# nessuno: le legge l'app dal file. Se un giorno servissero davvero (per aprire
# il database a mano, o per rimettere in piedi un backup fuori dall'app) stanno
# scritte in chiaro dentro .env, accanto a questo script.
#
#   ./setup.sh && docker compose up -d --build
#
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  echo "C'e' gia' un .env: lo lascio com'e'."
  echo "Se vuoi rigenerarlo, spostalo altrove e rilancia questo script."
  exit 0
fi

if [ ! -f .env.example ]; then
  echo "Manca .env.example: sei nella cartella giusta?" >&2
  exit 1
fi

# 32 caratteri presi a caso. Niente simboli: finiscono dentro una stringa di
# connessione, e una parentesi o una chiocciola li' in mezzo la spezzerebbe.
# Si legge una quantita' finita di byte invece di troncare un flusso infinito:
# chiudere il tubo sotto il naso di tr farebbe fallire lo script con pipefail.
genera_password() {
  head -c 4096 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c 1-32
}

proprietario="$(genera_password)"
applicazione="$(genera_password)"

sed -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${proprietario}|" \
    -e "s|^MONEY_APP_DB_PASSWORD=.*|MONEY_APP_DB_PASSWORD=${applicazione}|" \
    .env.example > .env
chmod 600 .env

echo "Creato .env con due password casuali."
echo
echo "Ora:  docker compose up -d --build"
echo "Poi apri http://localhost:3010 e crea il primo account."
