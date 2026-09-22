# app/normalizza.py
"""Da come risponde ARS a come conviene leggerlo a un modello.

Tre cose che un modello fa male: i conti con le date, il conteggio di un
elenco lungo e la lettura di una struttura che cambia forma. Qui l'eta' dell'ultimo punto di ripristino e' gia'
calcolata e scritta anche in lettere (`da_quanto`), e `data` viene accettata
sia come lista sia come oggetto con chiavi "0", "1", ...  I totali sono
contati qui.

Funzioni pure: prendono la risposta di ARS e l'istante, e restituiscono un
dizionario.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

# Dentro `data` ARS infila anche l'output del comando, in HTML. Non e' un
# repository e non deve arrivare al modello.
CHIAVI_NON_REPOSITORY = {"command_message"}


def adesso() -> datetime:
    """L'ora locale con il suo fuso: nel container e' quella di ARS."""
    return datetime.now().astimezone()


def _righe(data: Any) -> list[dict]:
    """Le righe di `data`, che ARS manda come lista o come oggetto."""
    if isinstance(data, dict):
        return [
            valore
            for chiave, valore in data.items()
            if chiave not in CHIAVI_NON_REPOSITORY and isinstance(valore, dict)
        ]
    if isinstance(data, list):
        return [valore for valore in data if isinstance(valore, dict)]
    return []


def _quando(iso: Any) -> Optional[datetime]:
    if not isinstance(iso, str) or not iso:
        return None
    try:
        quando = datetime.fromisoformat(iso)
    except ValueError:
        return None
    # Una data senza fuso si legge come ora locale, che e' quella di ARS.
    return quando.astimezone() if quando.tzinfo is None else quando


def _da_quanto(ore: Optional[float]) -> str:
    """L'eta' in lettere, perche' il modello non faccia sottrazioni fra date.

    Riceve lo stesso numero gia' arrotondato che finisce in `ore_fa`: se qui
    si troncasse il valore pieno, le due chiavi direbbero cose diverse --
    3,98 ore diventava `ore_fa: 4.0` ma `da_quanto: "3 ore"`, e il modello
    ha scritto "4.0 (da 3 ore)". Misurato il 22/09/2026.
    """
    if ore is None:
        return "mai"
    if ore < 0:
        return "data nel futuro"
    if ore < 1:
        return "meno di un'ora"
    if ore < 24:
        intere = int(ore)
        return "1 ora" if intere == 1 else f"{intere} ore"
    giorni, resto = divmod(int(ore), 24)
    testo = "1 giorno" if giorni == 1 else f"{giorni} giorni"
    if resto:
        testo += " e 1 ora" if resto == 1 else f" e {resto} ore"
    return testo


def _backup(riga: dict, ora: datetime, repository: Optional[str] = None) -> dict:
    """Una riga di backup, che si deve poter leggere da sola.

    Il nome del repository e' ripetuto dentro ogni riga anche quando le righe
    sono gia' raggruppate per repository. Non e' ridondanza inutile: lo stesso
    backup esiste in piu' repository con date molto diverse -- `nb-daniele` di
    Opero era di un'ora in uno e di quattro giorni in un altro -- e un modello
    che appiattisce l'elenco attribuisce la data al repository sbagliato.
    """
    quando = _quando(riga.get("lastSavePointCreatedDate"))
    ore = None
    if quando is not None:
        # `+ 0.0` toglie il meno da -0.0, che e' quello che esce quando
        # l'orologio di Nakivo e' avanti di un minuto sul nostro.
        ore = round((ora - quando).total_seconds() / 3600, 1) + 0.0
    voce = {"nome": riga.get("name")}
    if repository is not None:
        voce["repository"] = repository
    voce.update(
        {
            "job": riga.get("jobName"),
            "ultimo_punto": riga.get("lastSavePointCreatedDate"),
            "ore_fa": ore,
            "da_quanto": _da_quanto(ore),
        }
    )
    return voce


def _omonimi(repository: list[dict]) -> dict[str, int]:
    """Quali nomi stanno in piu' di un repository, e in quanti.

    Sta nella risposta perche' il modello, chiesto di un backup per nome,
    ne riporta uno solo anche avendo davanti tutte le righe: la domanda
    e' al singolare e lui si ferma alla prima. Con l'elenco davanti sa
    prima di cercare che quel nome va cercato piu' volte.

    Si contano i repository, non le righe: su Ready Net lo stesso backup
    compare due volte *dentro* lo stesso repository, e quello non e' un
    omonimo, e' un doppione dei dati di partenza.
    """
    dove: dict[str, set] = {}
    for riga in repository:
        for voce in riga["backup"]:
            if voce.get("nome"):
                dove.setdefault(voce["nome"], set()).add(riga["nome"])
    return {nome: len(posti) for nome, posti in dove.items() if len(posti) > 1}


def stato(data: Any, ora: Optional[datetime] = None) -> dict:
    """La risposta di `backup-status`: i repository con dentro i loro backup."""
    ora = ora or adesso()
    repository = [
        {
            "id": riga.get("id"),
            "nome": riga.get("name"),
            "totale_backup": len(_righe(riga.get("backups"))),
            "backup": [
                _backup(b, ora, riga.get("name")) for b in _righe(riga.get("backups"))
            ],
        }
        for riga in _righe(data)
    ]
    return {
        "ora_attuale": ora.isoformat(timespec="seconds"),
        "totale_repository": len(repository),
        "totale_backup": sum(r["totale_backup"] for r in repository),
        "nomi_in_piu_repository": _omonimi(repository),
        "repository": repository,
    }


def repository(data: Any) -> dict:
    """La risposta di `repositories/all`: solo id e nome."""
    elenco = [
        {"id": riga.get("id"), "nome": riga.get("name")} for riga in _righe(data)
    ]
    return {"totale_repository": len(elenco), "repository": elenco}


def backup(data: Any, repository_id: int, ora: Optional[datetime] = None) -> dict:
    """La risposta di `repositories/backups`: i backup di un repository."""
    ora = ora or adesso()
    righe = [_backup(riga, ora) for riga in _righe(data)]
    return {
        "ora_attuale": ora.isoformat(timespec="seconds"),
        "repository_id": repository_id,
        "totale_backup": len(righe),
        "backup": righe,
    }
