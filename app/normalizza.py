# app/normalizza.py
"""Da come risponde ARS a come conviene leggerlo a un modello.

Un modello sbaglia facilmente i conti con le date, il conteggio di un elenco
lungo, la lettura di una struttura che cambia forma e la scelta di cosa
segnalare. Per questo qui l'eta' dell'ultimo punto di ripristino e' gia'
calcolata e scritta anche in lettere (`da_quanto`), piattaforma e tipo di ogni
backup sono in parole, i totali sono contati, anche per piattaforma, le righe
identiche sono unite con il loro numero, i backup non aggiornati e quelli non
accessibili sono segnati riga per riga e contati, ogni riga ha gia' il suo
pallino colorato, i repository vuoti e quelli non accessibili sono gia'
scelti, i membri di un repository federato stanno dentro il federato, e
`data` si accetta sia come lista sia come oggetto con chiavi "0", "1", ...

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

# Il tipo di repository che si riporta, come lo manda ARS (minuscolo): solo il
# federato, fatto di piu' repository, i suoi membri. I backup stanno nel
# federato, e i membri non ne hanno di propri. Gli altri tipi (locale,
# condivisione di rete, S3) non dicono niente sullo stato dei backup.
FEDERATO = "federated"

# Lo stato del repository; "ok" non si riporta, perche' non c'e' niente da dire.
STATI_REPOSITORY = {
    "inaccessible": "non accessibile",
    "none": "sconosciuto",
}

# Il pallino di ogni riga, con i colori della dashboard di ARS. Serve solo a
# verificare quelli del rapportino, che il modello scrive con le stesse emoji:
# il client lo toglie prima che il risultato arrivi al modello.
VERDE, GIALLO, ROSSO = "🟢", "🟡", "🔴"


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

    Uguali vuol dire stesso nome, job, ultimo punto, piattaforma, tipo e
    accessibilita'. Lo stesso nome con un tipo diverso e' un altro elemento,
    per esempio la posta e il OneDrive della stessa persona, e resta una riga
    a se'. Riassumendo un
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
            riga.get("isAccessible"),
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
    """Se l'ultimo punto ha almeno l'eta' della soglia, o non c'e'.

    `ore_fa` e' arrotondato al decimo: 23,96 ore diventano 24,0, che
    `_da_quanto` scrive "1 giorno". Con `>` quella riga direbbe un giorno
    senza essere segnata.
    """
    return ore_fa is None or ore_fa >= soglia_ore


def _pallino(voce: dict, soglia_ore: float, soglia_rosso_ore: float) -> str:
    """Rosso se il backup non e' accessibile, non ha punti o ha almeno
    `soglia_rosso_ore`; giallo se non e' aggiornato; verde altrimenti."""
    if voce.get("non_accessibile") or _vecchio(voce["ore_fa"], soglia_rosso_ore):
        return ROSSO
    if _vecchio(voce["ore_fa"], soglia_ore):
        return GIALLO
    return VERDE


def _backup_uniti(
    righe: list[dict],
    ora: datetime,
    soglia_ore: float,
    soglia_rosso_ore: float,
    repository: Optional[str] = None,
) -> list[dict]:
    """Le righe di backup di un repository, con quelle identiche unite.

    `righe` c'e' solo quando vale piu' di 1, e `non_aggiornato` e
    `non_accessibile` solo quando sono veri, cosi' le righe senza niente da
    dire restano come sono. Il confronto con le soglie lo fa il server:
    nell'elenco completo il modello riconosce i backup da segnalare, e il
    colore di ognuno, senza confrontare le date da se'. Non accessibile e'
    solo il backup per cui ARS manda `isAccessible` falso: senza il campo non
    si segna niente.

    Il pallino e' la prima chiave della riga, come e' il primo segno della
    riga scritta nel rapportino.
    """
    elenco = []
    for riga, quante in _uniche(righe):
        voce = _backup(riga, ora, repository)
        if quante > 1:
            voce["righe"] = quante
        if _vecchio(voce["ore_fa"], soglia_ore):
            voce["non_aggiornato"] = True
        if riga.get("isAccessible") is False:
            voce["non_accessibile"] = True
        elenco.append({"pallino": _pallino(voce, soglia_ore, soglia_rosso_ore), **voce})
    return elenco


def _contate(repository: list[dict], segno: str) -> int:
    """Quante righe di ARS hanno `segno`, contando anche quelle unite."""
    return sum(
        voce.get("righe", 1)
        for riga in repository
        for voce in riga["backup"]
        if voce.get(segno)
    )


def _parole(codice: Any, tabella: dict[str, str]) -> Optional[str]:
    """Un codice di ARS in parole; quello che la tabella non conosce passa com'e'."""
    if not isinstance(codice, str) or not codice:
        return None
    return tabella.get(codice.lower(), codice)


def _federazione(repositories: Any) -> tuple[dict[Any, Any], dict[Any, dict]]:
    """Da `repositories/all`: di quale federato e' membro ogni repository, e i
    dati di ogni repository per id.

    Un membro porta l'id del suo federato in `federated_repository_data.id`.
    """
    membro_di: dict[Any, Any] = {}
    per_id: dict[Any, dict] = {}
    for riga in _righe(repositories):
        per_id[riga.get("id")] = riga
        federato = (riga.get("federated_repository_data") or {}).get("id")
        if federato is not None and federato != riga.get("id"):
            membro_di[riga.get("id")] = federato
    return membro_di, per_id


def _stato_repository(riga: dict, dati: Optional[dict]) -> Optional[str]:
    """Lo stato in parole, o None se e' ok o non si sa. `backup-status` lo
    chiama `status`, `repositories/all` `state`."""
    codice = riga.get("status") or riga.get("state") or (dati or {}).get("state")
    if not isinstance(codice, str) or codice.lower() == "ok":
        return None
    return _parole(codice, STATI_REPOSITORY)


def _descrivi(riga: dict, dati: Optional[dict]) -> dict:
    """Nome, tipo e stato di un repository; il tipo solo se e' un federato, lo
    stato solo se non e' ok."""
    voce: dict[str, Any] = {"nome": riga.get("name")}
    tipo = riga.get("type") or (dati or {}).get("type")
    if isinstance(tipo, str) and tipo.lower() == FEDERATO:
        voce["tipo"] = "federato"
    stato_repository = _stato_repository(riga, dati)
    if stato_repository is not None:
        voce["stato"] = stato_repository
    return voce


def stato(
    data: Any,
    ora: Optional[datetime] = None,
    soglia_ore: float = 48.0,
    repositories: Any = None,
    soglia_rosso_ore: float = 96.0,
) -> dict:
    """La risposta di `backup-status`: i repository con dentro i loro backup.

    `repositories` e' la risposta di `repositories/all`, l'unica che dice di
    quale federato e' membro un repository. Con quella, un membro senza backup
    sta solo dentro il suo federato, in `membri`, e non conta fra i
    repository ne' fra quelli vuoti: i suoi backup sono quelli del federato.
    Senza (ARS non l'ha data), i repository restano tutti in fila, com'erano.

    In testa, prima dell'elenco, i totali e quello che va segnalato: quanti
    backup non sono aggiornati secondo `soglia_ore`, quanti non sono
    accessibili, i repository vuoti e quelli non accessibili. Quali backup
    siano lo dicono `non_aggiornato` e `non_accessibile` nelle loro righe,
    dentro l'elenco completo: un elenco a parte il modello lo riporterebbe al
    posto di quello completo, o in aggiunta. Il pallino di ogni riga si
    colora con `soglia_ore` e `soglia_rosso_ore`.
    """
    ora = ora or adesso()
    membro_di, per_id = _federazione(repositories)
    letti = [(riga, _righe(riga.get("backups"))) for riga in _righe(data)]
    nomi_per_id = {riga.get("id"): riga.get("name") for riga, _ in letti}
    # Il federato di ogni membro, se anche il federato e' fra i repository.
    federato = {
        riga.get("id"): membro_di[riga.get("id")]
        for riga, _ in letti
        if membro_di.get(riga.get("id")) in nomi_per_id
    }
    membri: dict[Any, list[dict]] = {}
    for riga, _ in letti:
        if riga.get("id") in federato:
            membri.setdefault(federato[riga.get("id")], []).append(
                _descrivi(riga, per_id.get(riga.get("id")))
            )

    repository = []
    non_accessibili = []
    for riga, backup in letti:
        voce = _descrivi(riga, per_id.get(riga.get("id")))
        if voce.get("stato") == STATI_REPOSITORY["inaccessible"]:
            segnalato = {"nome": voce["nome"]}
            if riga.get("id") in federato:
                segnalato["membro_di"] = nomi_per_id[federato[riga.get("id")]]
            non_accessibili.append(segnalato)
        # Un membro senza backup sta solo dentro il federato; uno con dei
        # backup suoi resta anche in fila, perche' non si perdano.
        if riga.get("id") in federato and not backup:
            continue
        if riga.get("id") in membri:
            voce["membri"] = membri[riga.get("id")]
        voce.update(
            {
                # Il totale conta le righe di ARS, anche quelle poi unite.
                "totale_backup": len(backup),
                "backup": _backup_uniti(backup, ora, soglia_ore, soglia_rosso_ore, riga.get("name")),
            }
        )
        repository.append(voce)

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
            "repository_non_accessibili": non_accessibili,
            # Come `totale_backup`, contano le righe di ARS, anche quelle unite.
            "totale_non_aggiornati": _contate(repository, "non_aggiornato"),
            "totale_non_accessibili": _contate(repository, "non_accessibile"),
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
    data: Any,
    repository: str,
    ora: Optional[datetime] = None,
    soglia_ore: float = 48.0,
    repositories: Any = None,
    soglia_rosso_ore: float = 96.0,
) -> dict:
    """La risposta di `repositories/backups`: i backup di un repository.

    Da `repositories/all` vengono tipo e stato del repository, il federato di
    cui e' membro (`membro_di`: i backup stanno li') o i suoi membri.
    """
    ora = ora or adesso()
    righe = _righe(data)
    risposta: dict[str, Any] = {
        "ora_attuale": ora.isoformat(timespec="seconds"),
        "repository": repository,
    }
    membro_di, per_id = _federazione(repositories)
    dati = next((r for r in per_id.values() if r.get("name") == repository), None)
    if dati is not None:
        descritto = _descrivi(dati, None)
        for chiave in ("tipo", "stato"):
            if chiave in descritto:
                risposta[chiave] = descritto[chiave]
        federato = per_id.get(membro_di.get(dati.get("id")))
        if federato is not None:
            risposta["membro_di"] = federato.get("name")
        membri = [
            _descrivi(per_id[membro], None)
            for membro, padre in membro_di.items()
            if padre == dati.get("id") and membro in per_id
        ]
        if membri:
            risposta["membri"] = membri
    risposta["totale_backup"] = len(righe)
    per_piattaforma = _per_piattaforma(righe)
    if per_piattaforma:
        risposta["backup_per_piattaforma"] = per_piattaforma
    risposta["backup"] = _backup_uniti(righe, ora, soglia_ore, soglia_rosso_ore, repository)
    return risposta


def per_nome(
    data: Any,
    nome: str,
    ora: Optional[datetime] = None,
    soglia_ore: float = 48.0,
    soglia_rosso_ore: float = 96.0,
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
                    "backup": _backup_uniti(suoi, ora, soglia_ore, soglia_rosso_ore, riga.get("name")),
                }
            )
    return {
        "ora_attuale": ora.isoformat(timespec="seconds"),
        "cercato": nome.strip(),
        "nomi_trovati": sorted(scelti),
        "totale_backup": sum(r["totale_backup"] for r in repository),
        "totale_non_aggiornati": _contate(repository, "non_aggiornato"),
        "totale_non_accessibili": _contate(repository, "non_accessibile"),
        "repository": repository,
    }
