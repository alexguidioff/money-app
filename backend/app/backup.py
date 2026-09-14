"""Creazione e ripristino dei dump del database.

I dump sono file in formato `pg_dump --format=custom` scritti in `/backups`,
un volume condiviso fra i servizi. `pg_dump` e `pg_restore` girano dentro
questo stesso container e parlano al database via rete: nessun socket Docker,
nessun privilegio sulla macchina che ospita l'app, nessuna dipendenza dal nome
che il gestore di container ha dato al servizio del database.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url


BACKUPS_DIR = Path(os.getenv("MONEY_BACKUPS_DIR", "/backups"))


def _connection() -> dict[str, str]:
    """Parametri di connessione, letti dallo stesso DATABASE_URL dell'app.

    Cosi' backup e applicazione non possono puntare a due database diversi.
    """
    # Deliberatamente la connessione del proprietario: con il ruolo ristretto
    # le politiche di riga filtrerebbero le righe e il dump conterrebbe solo
    # i dati di un utente, senza che nulla lo segnali.
    predefinita = "postgresql+psycopg://money:money-local@db:5432/money"
    url = make_url(os.getenv("DATABASE_ADMIN_URL") or os.getenv("DATABASE_URL", predefinita))
    return {
        "host": url.host or "db",
        "port": str(url.port or 5432),
        "user": url.username or "money",
        "password": url.password or "",
        "dbname": url.database or "money",
    }


def _psql_env() -> dict[str, str]:
    conn = _connection()
    return {**os.environ, "PGPASSWORD": conn["password"]}


def _target_args() -> list[str]:
    conn = _connection()
    return ["-h", conn["host"], "-p", conn["port"], "-U", conn["user"], "-d", conn["dbname"]]


def _safe_label(label: str) -> str:
    """Filename safe: solo lettere, numeri, trattini e underscore."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", label.strip().lower())
    return cleaned.strip("-") or "manual"


def create_backup(label: str = "manual") -> dict:
    """Crea un dump del database e ritorna i metadati del file creato."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filename = f"{_safe_label(label)}-{timestamp}.dump"
    target = BACKUPS_DIR / filename

    cmd = ["pg_dump", *_target_args(), "-Fc"]
    with target.open("wb") as fh:
        result = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, check=False, env=_psql_env())
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"pg_dump terminato con codice {result.returncode}: "
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )

    size = target.stat().st_size
    return {
        "success": True,
        "label": _safe_label(label),
        "filename": filename,
        "size_bytes": size,
        "created_at": timestamp,
        "path": str(target),
    }


def list_backups() -> list[dict]:
    """Elenca i dump presenti nella cartella backup, ordinati dal più recente."""
    if not BACKUPS_DIR.exists():
        return []
    backups = []
    # Per data, non per nome: ordinare alfabeticamente metterebbe un dump
    # "prova" davanti a un "auto" piu' recente, e la ritenzione cancella
    # partendo dal fondo di questa lista.
    for path in sorted(BACKUPS_DIR.glob("*.dump"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        backups.append({
            "filename": path.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
            "path": str(path),
        })
    return backups


def _percorso_dump(filename: str) -> Path:
    """Il file dentro la cartella dei backup, e nient'altro.

    Il nome arriva dall'URL: senza questo controllo un `../` porterebbe a
    leggere - o cancellare - file fuori dalla cartella.
    """
    safe = Path(filename).name
    if safe != filename or "/" in filename or ".." in filename:
        raise ValueError("Nome file non valido")
    target = BACKUPS_DIR / safe
    if not target.exists():
        raise FileNotFoundError(f"Backup non trovato: {safe}")
    return target


def delete_backup(filename: str) -> dict:
    """Cancella un dump. Irreversibile: la conferma la chiede il chiamante."""
    target = _percorso_dump(filename)
    target.unlink()
    return {"deleted": target.name}


def restore_backup(filename: str) -> dict:
    """Ripristina un dump. Richiede conferma esplicita lato chiamante."""
    target = _percorso_dump(filename)

    # Prima verifica l'archivio, poi conserva lo stato attuale.
    check = subprocess.run(["pg_restore", "--list", str(target)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if check.returncode != 0:
        raise ValueError("Backup non leggibile")
    safety = create_backup("pre-restore")
    user = _connection()["user"]
    restore = subprocess.run(
        ["pg_restore", *_target_args(), "--clean", "--if-exists", "--single-transaction",
         "--exit-on-error", "--no-owner", "--role", user, str(target)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=_psql_env(),
    )
    if restore.returncode != 0:
        raise RuntimeError("Ripristino annullato: " + restore.stderr.decode("utf-8", errors="replace").strip())
    return {
        "success": True,
        "filename": target.name,
        "backup": safety["filename"],
        "restored_at": datetime.utcnow().isoformat() + "Z",
    }


# Ora che l'Excel non e' piu' la seconda copia dei dati, il dump e' l'unica
# rete di sicurezza: deve esistere senza che nessuno si ricordi di crearlo.
AUTO_LABEL = "auto"
AUTO_INTERVAL = timedelta(days=1)
KEEP_AUTOMATIC = 7          # una settimana di dump giornalieri
KEEP_PRE_IMPORT = 5         # i dump presi prima di sostituire i dati


def _label_of(filename: str) -> str:
    """Il nome e' `<etichetta>-<timestamp>.dump`: torna l'etichetta."""
    return filename.rsplit("-", 1)[0] if "-" in filename else filename


def prune_backups() -> list[str]:
    """Tiene gli ultimi dump di ogni categoria e cancella i piu' vecchi.

    I backup manuali non si toccano: li ha chiesti qualcuno di proposito.
    """
    limits = {AUTO_LABEL: KEEP_AUTOMATIC, "pre-import": KEEP_PRE_IMPORT}
    seen: dict[str, int] = {}
    removed: list[str] = []
    for backup in list_backups():           # gia' ordinati dal piu' recente
        label = _label_of(backup["filename"].removesuffix(".dump"))
        limit = limits.get(label)
        if limit is None:
            continue
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > limit:
            Path(backup["path"]).unlink(missing_ok=True)
            removed.append(backup["filename"])
    return removed


def last_backup(label: str | None = None) -> dict | None:
    for backup in list_backups():
        if label is None or _label_of(backup["filename"].removesuffix(".dump")) == label:
            return backup
    return None


def ensure_daily_backup() -> dict | None:
    """Crea il dump giornaliero se manca. Ritorna None se ce n'e' gia' uno recente."""
    latest = last_backup(AUTO_LABEL)
    if latest:
        age = datetime.utcnow() - datetime.fromisoformat(latest["created_at"].removesuffix("Z"))
        if age < AUTO_INTERVAL:
            return None
    result = create_backup(AUTO_LABEL)
    prune_backups()
    return result
