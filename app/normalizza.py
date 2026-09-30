# app/normalizza.py
"""Da come risponde ARS a come conviene leggerlo a un modello.

Un modello sbaglia facilmente i conti con le date, il conteggio di un elenco
lungo, la lettura di una struttura che cambia forma e la scelta di cosa
segnalare. Per questo qui l'eta' dell'ultimo punto di ripristino e' gia'
calcolata e scritta anche in lettere (`da_quanto`), piattaforma e tipo di ogni
backup sono in parole, i totali sono contati, anche per piattaforma, le righe
identiche sono unite con il loro numero, i backup non aggiornati sono segnati
riga per riga e contati, i repository vuoti sono gia' scelti, e `data` si
accetta sia come lista sia come oggetto con chiavi "0", "1", ...

Funzioni pure: prendono la risposta di ARS e l'istante, e restituiscono un
dizionario.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Optional

from .exceptions import BackupNonTrovato, RepositoryNonTrovato

# Dentro `data` ARS infila anche l'output del comando, in HTML. Non e' un
# repository e non deve arrivare al modello.
CHIAVI_NON_REPOSITORY = {"command_message"}

# `hvType` e `sourceType` arrivano da ARS come li scrive Nakivo. Qui diventano
# parole, perche' il modello ricopierebbe i codici tali e quali, anche nei
# rapportini. Un codice che manca da queste tabelle passa com'e'.
PIATTAFORME = {
    "VMWARE": "VMware",
    "OFFICE365": "Microsoft 365",
    "PHYSICAL": "macchine fisiche",
    "NAS": "NAS",
}

# `VM_BACKUP` vale per le macchine virtuali, per quelle fisiche e per le
# cartelle dei NAS: il tipo dipende anche dalla piattaforma.
TIPI = {
    ("VMWARE", "VM_BACKUP"): "macchina virtuale",
    ("PHYSICAL", "VM_BACKUP"): "macchina fisica",
    ("NAS", "VM_BACKUP"): "cartella condivisa",
    ("OFFICE365", "OUTLOOK_USER"): "posta",
    ("OFFICE365", "ONE_DRIVE"): "OneDrive",
    ("OFFICE365", "O365_TEAMS"): "Teams",
    ("OFFICE365", "O365_GROUP"): "gruppo Microsoft 365",
    ("OFFICE365", "SHARE_POINT"): "sito SharePoint",
    ("OFFICE365", "SHARE_POINT_GROUP"): "sito SharePoint del gruppo",
    ("OFFICE365", "SHARE_POINT_PERSONAL"): "sito SharePoint personale",
}

# Nei totali per piattaforma, le righe che ARS manda senza `hvType`.
PIATTAFORMA_NON_INDICATA = "non indicata"


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


def _codice(valore: Any) -> Optional[str]:
    """Un codice di Nakivo, o None se ARS non lo manda."""
    return valore if isinstance(valore, str) and valore else None


def _piattaforma(riga: dict) -> Optional[str]:
    """La piattaforma del backup in parole: VMware, Microsoft 365, ..."""
    codice = _codice(riga.get("hvType"))
    return PIATTAFORME.get(codice, codice) if codice else None


def _tipo(riga: dict) -> Optional[str]:
    """Cosa e' stato salvato, in parole: macchina virtuale, posta, OneDrive, ..."""
    codice = _codice(riga.get("sourceType"))
    if codice is None:
        return None
    return TIPI.get((_codice(riga.get("hvType")), codice), codice)


def _per_piattaforma(righe: list[dict]) -> dict[str, int]:
    """Quante righe di ARS per piattaforma, dalla piu' numerosa.

    Vuoto se ARS non manda la piattaforma di nessuna riga. Se la manda solo per
    alcune, le altre stanno sotto "non indicata", cosi' la somma torna col
    totale dei backup.
    """
    conteggi = Counter(_piattaforma(riga) or PIATTAFORMA_NON_INDICATA for riga in righe)
    if not conteggi or set(conteggi) == {PIATTAFORMA_NON_INDICATA}:
        return {}
    return dict(conteggi.most_common())


def _uniche(righe: list[dict]) -> list[tuple[dict, int]]:
    """Le righe uguali in tutto, tipo compreso, unite: ognuna col suo numero.

    Uguali vuol dire stesso nome, job, ultimo punto, piattaforma e tipo. Lo
    stesso nome con un tipo diverso e' un altro elemento, per esempio la posta
    e il OneDrive della stessa persona, e resta una riga a se'. Riassumendo un
    elenco lungo il modello raggruppa da se' le righe uguali e le conta male:
    qui arrivano gia' contate. L'ordine e' quello della prima comparsa.
    """
    conteggi: dict[tuple, list] = {}
    for riga in righe:
        chiave = (
            riga.get("name"),
            riga.get("jobName"),
            riga.get("lastSavePointCreatedDate"),
            _codice(riga.get("hvType")),
            _codice(riga.get("sourceType")),
        )
        if chiave in conteggi:
            conteggi[chiave][1] += 1
        else:
            conteggi[chiave] = [riga, 1]
    return [(riga, quante) for riga, quante in conteggi.values()]


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
    # Piattaforma e tipo ci sono solo se ARS li manda.
    piattaforma, tipo = _piattaforma(riga), _tipo(riga)
    if piattaforma is not None:
        voce["piattaforma"] = piattaforma
    if tipo is not None:
        voce["tipo"] = tipo
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


def _vecchio(ore_fa: Optional[float], soglia_ore: float) -> bool:
    """Se l'ultimo punto e' piu' vecchio della soglia, o non c'e'."""
    return ore_fa is None or ore_fa > soglia_ore


def _backup_uniti(
    righe: list[dict], ora: datetime, soglia_ore: float, repository: Optional[str] = None
) -> list[dict]:
    """Le righe di backup di un repository, con quelle identiche unite.

    `righe` c'e' solo quando vale piu' di 1, e `non_aggiornato` solo quando
    e' vero, cosi' le righe senza niente da dire restano come sono. Il
    confronto con la soglia lo fa il server: nell'elenco completo il modello
    riconosce i backup da segnalare senza confrontare le date da se'.
    """
    elenco = []
    for riga, quante in _uniche(righe):
        voce = _backup(riga, ora, repository)
        if quante > 1:
            voce["righe"] = quante
        if _vecchio(voce["ore_fa"], soglia_ore):
            voce["non_aggiornato"] = True
        elenco.append(voce)
    return elenco


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


def _soglia(soglia_ore: float) -> float:
    """24 e non 24.0: il modello scrive la soglia come la legge."""
    return int(soglia_ore) if float(soglia_ore).is_integer() else soglia_ore


def stato(data: Any, ora: Optional[datetime] = None, soglia_ore: float = 24.0) -> dict:
    """La risposta di `backup-status`: i repository con dentro i loro backup.

    In testa, prima dell'elenco, i totali e quello che va segnalato: quanti
    backup non sono aggiornati secondo `soglia_ore` e i repository vuoti.
    Quali siano lo dice `non_aggiornato` nelle loro righe, dentro l'elenco
    completo: un elenco a parte il modello lo riporterebbe al posto di quello
    completo, o in aggiunta.
    """
    ora = ora or adesso()
    letti = [(riga, _righe(riga.get("backups"))) for riga in _righe(data)]
    repository = [
        {
            "nome": riga.get("name"),
            # Il totale conta le righe di ARS, anche quelle poi unite.
            "totale_backup": len(backup),
            "backup": _backup_uniti(backup, ora, soglia_ore, riga.get("name")),
        }
        for riga, backup in letti
    ]
    vuoti = [r["nome"] for r in repository if r["totale_backup"] == 0]
    risposta = {
        "ora_attuale": ora.isoformat(timespec="seconds"),
        "totale_repository": len(repository),
        "totale_backup": sum(r["totale_backup"] for r in repository),
    }
    per_piattaforma = _per_piattaforma([b for _, backup in letti for b in backup])
    if per_piattaforma:
        risposta["backup_per_piattaforma"] = per_piattaforma
    risposta.update(
        {
            "repository_con_backup": len(repository) - len(vuoti),
            "repository_vuoti": vuoti,
            "soglia_ore": _soglia(soglia_ore),
            # Come `totale_backup`, conta le righe di ARS, anche quelle unite.
            "totale_non_aggiornati": sum(
                voce.get("righe", 1)
                for riga in repository
                for voce in riga["backup"]
                if voce.get("non_aggiornato")
            ),
            "nomi_in_piu_repository": _omonimi(repository),
            "repository": repository,
        }
    )
    return risposta


def cerca_repository(data: Any, nome: str) -> dict:
    """Fra i repository di `repositories/all`, quello col nome dato: id e nome.

    Il confronto ignora maiuscole e spazi ai bordi. Se nessun nome e'
    identico vale quello che contiene il testo cercato, purche' sia uno solo:
    "nasbackup.opero" indica "nasbackup.opero.local", "nasbackup" no se i
    repository che lo contengono sono due. Altrimenti `RepositoryNonTrovato`,
    con i nomi fra cui scegliere.
    """
    elenco = [
        {"id": riga.get("id"), "nome": riga.get("name")}
        for riga in _righe(data)
        if isinstance(riga.get("name"), str)
    ]
    if not elenco:
        raise RepositoryNonTrovato("Per questa azienda non risulta nessun repository Nakivo.")
    cercato = nome.strip().casefold()
    identici = [r for r in elenco if r["nome"].strip().casefold() == cercato]
    if len(identici) == 1:
        return identici[0]
    simili = [r for r in elenco if cercato and cercato in r["nome"].casefold()]
    if len(simili) == 1:
        return simili[0]
    if len(simili) > 1:
        nomi = ", ".join(r["nome"] for r in simili)
        raise RepositoryNonTrovato(
            f"\"{nome}\" corrisponde a più repository: {nomi}. Serve il nome completo."
        )
    nomi = ", ".join(r["nome"] for r in elenco)
    raise RepositoryNonTrovato(
        f"Nessun repository dell'azienda si chiama \"{nome}\". I repository sono: {nomi}."
    )


def backup(
    data: Any, repository: str, ora: Optional[datetime] = None, soglia_ore: float = 24.0
) -> dict:
    """La risposta di `repositories/backups`: i backup di un repository."""
    ora = ora or adesso()
    righe = _righe(data)
    risposta = {
        "ora_attuale": ora.isoformat(timespec="seconds"),
        "repository": repository,
        "totale_backup": len(righe),
    }
    per_piattaforma = _per_piattaforma(righe)
    if per_piattaforma:
        risposta["backup_per_piattaforma"] = per_piattaforma
    risposta["soglia_ore"] = _soglia(soglia_ore)
    risposta["backup"] = _backup_uniti(righe, ora, soglia_ore, repository)
    return risposta


def per_nome(
    data: Any, nome: str, ora: Optional[datetime] = None, soglia_ore: float = 24.0
) -> dict:
    """Da `backup-status`, i soli backup col nome cercato, divisi per repository.

    Un modello che cerca le righe di un nome in tutto l'elenco ne perde
    qualcuna: qui le sceglie il server. Il confronto ignora maiuscole e spazi
    ai bordi. Se qualche nome e' identico a quello cercato valgono solo quelli,
    altrimenti tutti quelli che lo contengono: "Pontarollo" trova "Massimiliano
    Pontarollo", "srv-web" non trova anche "srv-web2" se esiste "srv-web".
    `nomi_trovati` dice quali nomi sono.
    """
    ora = ora or adesso()
    cercato = nome.strip().casefold()
    letti = [(riga, _righe(riga.get("backups"))) for riga in _righe(data)]
    nomi = {
        b["name"]
        for _, backup in letti
        for b in backup
        if isinstance(b.get("name"), str) and cercato and cercato in b["name"].casefold()
    }
    identici = {n for n in nomi if n.strip().casefold() == cercato}
    scelti = identici or nomi
    if not scelti:
        raise BackupNonTrovato(f"Nessun backup dell'azienda ha \"{nome.strip()}\" nel nome.")
    repository = []
    for riga, backup in letti:
        suoi = [b for b in backup if b.get("name") in scelti]
        if suoi:
            repository.append(
                {
                    "nome": riga.get("name"),
                    "totale_backup": len(suoi),
                    "backup": _backup_uniti(suoi, ora, soglia_ore, riga.get("name")),
                }
            )
    return {
        "ora_attuale": ora.isoformat(timespec="seconds"),
        "cercato": nome.strip(),
        "nomi_trovati": sorted(scelti),
        "totale_backup": sum(r["totale_backup"] for r in repository),
        "soglia_ore": _soglia(soglia_ore),
        "totale_non_aggiornati": sum(
            voce.get("righe", 1)
            for riga in repository
            for voce in riga["backup"]
            if voce.get("non_aggiornato")
        ),
        "repository": repository,
    }
