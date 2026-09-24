# app/normalizza.py
"""Da come risponde ARS a come conviene leggerlo a un modello.

Un modello sbaglia facilmente i conti con le date, il conteggio di un elenco
lungo, la lettura di una struttura che cambia forma e la scelta di cosa
segnalare. Per questo qui l'eta' dell'ultimo punto di ripristino e' gia'
calcolata e scritta anche in lettere (`da_quanto`), i totali sono contati, i
backup da controllare e i repository vuoti sono gia' scelti, e `data` si
accetta sia come lista sia come oggetto con chiavi "0", "1", ...

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


def _leggibile(quando: datetime) -> str:
    """La data come la scrive una persona, nell'ora locale: "18/09/2026 09:00".

    In ISO il modello la ricopierebbe tale e quale, anche nei rapportini.
    """
    return quando.astimezone().strftime("%d/%m/%Y %H:%M")


def _da_quanto(ore: Optional[float]) -> str:
    """L'eta' in lettere, perche' il modello non faccia sottrazioni fra date.

    Riceve lo stesso numero gia' arrotondato che finisce in `ore_fa`:
    troncando il valore pieno, le due chiavi direbbero cose diverse (3,98 ore
    darebbe `ore_fa: 4.0` e `da_quanto: "3 ore"`).
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

    Il nome del repository e' ripetuto in ogni riga anche quando le righe sono
    gia' raggruppate per repository: lo stesso backup sta spesso in piu'
    repository con date molto diverse, e un modello che appiattisce l'elenco
    attribuirebbe la data al repository sbagliato.
    """
    quando = _quando(riga.get("lastSavePointCreatedDate"))
    ore = None
    if quando is not None:
        # `+ 0.0` toglie il meno da -0.0, che esce quando l'orologio di Nakivo
        # e' appena avanti rispetto a quello del server.
        ore = round((ora - quando).total_seconds() / 3600, 1) + 0.0
    voce = {"nome": riga.get("name")}
    if repository is not None:
        voce["repository"] = repository
    voce.update(
        {
            "job": riga.get("jobName"),
            # Una data che non si riesce a leggere passa com'e'.
            "ultimo_punto": (
                _leggibile(quando) if quando else riga.get("lastSavePointCreatedDate")
            ),
            "ore_fa": ore,
            "da_quanto": _da_quanto(ore),
        }
    )
    return voce


def _omonimi(repository: list[dict]) -> dict[str, int]:
    """Quali nomi stanno in piu' di un repository, e in quanti.

    Chiesto di un backup per nome, il modello tende a fermarsi alla prima
    riga che trova: con questo elenco sa in anticipo che quel nome va cercato
    piu' volte.

    Si contano i repository, non le righe: lo stesso nome ripetuto dentro un
    solo repository non e' un omonimo.
    """
    dove: dict[str, set] = {}
    for riga in repository:
        for voce in riga["backup"]:
            if voce.get("nome"):
                dove.setdefault(voce["nome"], set()).add(riga["nome"])
    return {nome: len(posti) for nome, posti in dove.items() if len(posti) > 1}


def _da_controllare(repository: list[dict], soglia_ore: float) -> list[dict]:
    """I backup da segnalare: l'ultimo punto e' piu' vecchio della soglia, o non c'e'.

    Arrivano gia' scelti perche' il modello, riassumendo un elenco lungo, li
    perde. Prima quelli senza punto di ripristino, poi dal piu' vecchio.
    """
    vecchi = [
        voce
        for riga in repository
        for voce in riga["backup"]
        if voce["ore_fa"] is None or voce["ore_fa"] > soglia_ore
    ]
    vecchi.sort(key=lambda voce: (voce["ore_fa"] is not None, -(voce["ore_fa"] or 0.0)))
    return [
        {chiave: voce.get(chiave) for chiave in ("nome", "repository", "ultimo_punto", "da_quanto")}
        for voce in vecchi
    ]


def stato(data: Any, ora: Optional[datetime] = None, soglia_ore: float = 24.0) -> dict:
    """La risposta di `backup-status`: i repository con dentro i loro backup.

    In testa, prima dell'elenco, quello che va segnalato: i backup da
    controllare secondo `soglia_ore` e i repository vuoti.
    """
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
    vuoti = [r["nome"] for r in repository if r["totale_backup"] == 0]
    return {
        "ora_attuale": ora.isoformat(timespec="seconds"),
        "totale_repository": len(repository),
        "totale_backup": sum(r["totale_backup"] for r in repository),
        "repository_con_backup": len(repository) - len(vuoti),
        "repository_vuoti": vuoti,
        "soglia_ore": soglia_ore,
        "da_controllare": _da_controllare(repository, soglia_ore),
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
