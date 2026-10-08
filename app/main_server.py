# app/main_server.py
"""Il server MCP: tre tool che leggono i backup Nakivo di un'azienda.

`company_id` e' un parametro di ogni tool, ma **non lo sceglie il modello**:
lo mette mcp-c-ars a ogni chiamata, prendendolo dalla richiesta che arriva da
ARS, dopo averlo tolto dallo schema che il modello vede. Qui l'azienda si
considera gia' decisa.

Da questo discende il perimetro del server: chiunque lo raggiunga puo' leggere
i backup di qualunque azienda passando un company_id. Per questo la porta non
si pubblica fuori dalla rete interna e le chiamate devono portare
`MCP_API_KEY`, il segreto condiviso con il client.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager, contextmanager

import fastmcp
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from starlette.responses import JSONResponse

from . import ars_client, normalizza
from .exceptions import ErroreNakivo
from .logger_config import setup_logging
from .settings import settings

setup_logging()

logger = logging.getLogger("NAKIVO")

fastmcp.settings.check_for_updates = "off"
fastmcp.settings.telemetry_mode = "off"

# I tool leggono e basta: nessuno di essi avvia backup o modifica qualcosa su
# Nakivo. `openWorldHint` perche' i dati arrivano da un sistema esterno.
SOLA_LETTURA = {
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}


@asynccontextmanager
async def ciclo_di_vita(_server: FastMCP):
    # L'ora locale si logga all'avvio perche' e' quella che finisce in
    # `ora_attuale` e che il modello riferisce: un container partito con il
    # fuso sbagliato si vede qui, non dalle risposte.
    logger.info(
        f"Server MCP Nakivo avviato. ARS: {settings.ARS_BASE_URL}. "
        f"Ora locale: {normalizza.adesso().isoformat(timespec='minutes')}. "
        f"Backup non aggiornati oltre {settings.SOGLIA_BACKUP_ORE:g} ore"
    )
    yield
    await ars_client.chiudi()
    logger.info("Server MCP Nakivo arrestato.")


# Una chiave sola, quella di mcp-c-ars: non c'e' un OAuth di mezzo, e' un
# segreto condiviso fra due container della stessa rete.
auth = StaticTokenVerifier(tokens={settings.MCP_API_KEY: {"client_id": "mcp-c-ars"}})

mcp = FastMCP(
    name="nakivo",
    instructions=(
        "Dati dei backup Nakivo dell'azienda selezionata in ARS. "
        "I tool leggono soltanto: non avviano backup e non modificano niente."
    ),
    auth=auth,
    lifespan=ciclo_di_vita,
    # Solo i messaggi di ToolError arrivano al modello; il testo di
    # un'eccezione imprevista resta nei log.
    mask_error_details=True,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(_richiesta):
    """Sonda per il healthcheck del container.

    Dice che il processo risponde, non che ARS sia raggiungibile: quello si
    vede dai tool, e un guasto di ARS non deve far riavviare questo container.
    """
    return JSONResponse({"stato": "ok"})


@contextmanager
def _traduci_errori():
    """Le eccezioni del server diventano errori del tool, con la loro frase.

    `ToolError` e' l'unica eccezione il cui messaggio FastMCP lascia passare
    al client: e' li' che la frase pensata per l'utente diventa la risposta
    del tool.
    """
    try:
        yield
    except ErroreNakivo as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=SOLA_LETTURA)
async def nakivo_stato_backup(company_id: int) -> dict:
    """Stato dei backup Nakivo dell'azienda: tutti i repository con i loro backup.

    È il tool per lo stato dei backup in generale. Per un backup preciso,
    dato il suo nome, usa `nakivo_backup_per_nome`; per un repository,
    `nakivo_backup_del_repository`.

    **In testa alla risposta ci sono i totali e quello che va segnalato:**
    `totale_repository`, `totale_backup`, `backup_per_piattaforma`,
    `repository_con_backup`, `repository_vuoti` (i repository senza backup),
    `repository_non_accessibili` (con `membro_di` se fanno parte di un
    repository federato), `totale_non_aggiornati`, quanti backup non sono
    aggiornati (il loro ultimo punto di ripristino è troppo vecchio, o non ce
    n'è nessuno), e `totale_non_accessibili`, quanti backup Nakivo segna come
    non accessibili. Riportali sempre, in ogni risposta sullo stato dei
    backup, con i loro valori; un repository non accessibile va sempre
    segnalato, col federato di cui fa parte. Gli altri campi in testa, come
    `ora_attuale`, non si riportano. Scrivili in un elenco puntato con
    queste etichette, mai coi nomi dei campi né con parentesi graffe o
    quadre, per esempio:

    - Repository: 7, di cui 4 con backup
    - Backup: 43 (VMware 28, Microsoft 365 14, macchine fisiche 1)
    - Repository vuoti: nas-a, nas-b
    - Repository non accessibili: nessuno
    - Backup non aggiornati: 0
    - Backup non accessibili: 0

    Un repository non accessibile membro di un federato si scrive «nome
    (membro di federato)».

    **Alla domanda sullo stato dei backup elenca tutti i backup**, repository
    per repository come stanno in `repository`, non solo quelli non
    aggiornati. Come si elencano, in questa e in ogni altra risposta: in
    Markdown, divisi per repository, con il nome del repository come titolo
    di quinto livello («##### nome») e sotto un elenco puntato con un backup
    per riga, nella forma «nome, tipo:
    ultimo punto, da quanto fa». Un backup sta su una riga sola, senza
    sottopunti. Le righe con `non_aggiornato` finiscono con «non aggiornato»
    in grassetto, quelle con `non_accessibile` con «non accessibile» in
    grassetto, e quelle con tutti e due con tutti e due: non ripeterle in un
    elenco a parte. Sotto un repository vuoto c'è una riga sola: «nessun
    backup».

    Un repository federato ha `tipo` «federato», e un repository che non è a
    posto il suo `stato` (non accessibile, sconosciuto): riportali accanto al
    nome. Un repository federato è fatto di più repository, i suoi `membri`,
    ognuno col suo stato se non è a posto: i backup stanno nel federato, e i
    membri non ne hanno di propri.
    Nomina i membri sotto il federato, prima dei suoi backup, e non contarli
    come repository vuoti.

    Se chiedono solo i backup non aggiornati, elenca solo le righe con
    `non_aggiornato`, nello stesso modo; se chiedono quelli non accessibili,
    solo le righe con `non_accessibile`. Se il loro totale è 0, dillo.

    **Un backup con lo stesso nome sta spesso in più repository, con date
    molto diverse.** Se ti chiedono di un backup per nome, usa
    `nakivo_backup_per_nome`, che le righe di quel nome le sceglie già.

    Ogni riga è già "l'ultimo backup" di quel nome in quel repository. Per un
    nome che sta in tre repository ci sono quindi tre ultimi backup, con tre
    date: chi chiede "quando è stato fatto l'ultimo backup di X" li vuole
    tutti e tre, anche quelli vecchi. Non sceglierne uno.

    Per ogni backup dice quando è stato creato l'ultimo punto di ripristino
    (`ultimo_punto`) e quanto tempo è passato (`ore_fa` e `da_quanto`, già
    calcolati: non rifare i conti con le date).

    Ogni riga porta il suo `repository`: cita sempre quale è e non prendere
    la data di un repository per un altro. Se un backup è non aggiornato lo
    dice `non_aggiornato` nella sua riga, anche quando lo stesso nome è
    recente in un altro repository: sono copie diverse. Lo stesso per
    `non_accessibile`.

    Ogni riga dice la piattaforma (`piattaforma`: VMware, Microsoft 365,
    macchine fisiche, NAS) e cosa è stato salvato (`tipo`: macchina virtuale,
    posta, OneDrive, sito SharePoint, ...). Lo stesso nome può stare più
    volte nello stesso repository con tipi diversi, per esempio la posta, il
    OneDrive e il sito SharePoint personale della stessa persona: sono
    elementi diversi, riportali ognuno col suo tipo.

    I totali sono già contati (`totale_repository`, `totale_backup`, uno per
    ogni repository, e `backup_per_piattaforma`, quanti backup per
    piattaforma): riporta quelli, non contare le righe a mano.

    Le righe identiche (stesso nome, tipo, repository e ultimo punto)
    arrivano unite in una sola, con `righe` che dice quante sono: sono
    elementi distinti che Nakivo chiama allo stesso modo. Riporta quel numero.
    `totale_backup` le conta tutte, una per una.

    I dati dicono solo quando è stato creato l'ultimo punto di ripristino:
    non dicono se il job è andato a buon fine, quindi non affermare che un
    backup è "riuscito" o "fallito".
    """
    logger.info(f"stato dei backup, azienda {company_id}")
    with _traduci_errori():
        data = await ars_client.chiama("/api/nakivo/backup-status", {"company_id": company_id})
    # Di quale federato e' membro un repository lo dice solo
    # `repositories/all`. Se non risponde, lo stato esce lo stesso, coi
    # repository tutti in fila.
    try:
        repositories = await ars_client.chiama(
            "/api/nakivo/repositories/all", {"company_id": company_id}
        )
    except ErroreNakivo as e:
        logger.warning(f"repository dell'azienda {company_id} non letti, stato senza federati: {e}")
        repositories = None
    return normalizza.stato(
        data,
        soglia_ore=settings.SOGLIA_BACKUP_ORE,
        repositories=repositories,
    )


@mcp.tool(annotations=SOLA_LETTURA)
async def nakivo_backup_per_nome(company_id: int, nome: str) -> dict:
    """I backup con un certo nome, in tutti i repository dell'azienda.

    È il tool per le domande su un backup preciso: "quando è stato fatto
    l'ultimo backup di X?", "com'è il backup di X?". `nome` è il nome come lo
    scrive l'utente, per esempio "Pontarollo" o "nb-ready15"; basta una
    parte. Se un nome è identico vale solo quello, altrimenti tutti quelli che
    contengono il testo: `nomi_trovati` dice quali sono, e se sono più di uno
    dillo.

    Le righe arrivano già scelte e divise per repository: riportale **tutte**,
    anche quelle vecchie, ognuna nel suo repository. Ogni riga è l'ultimo
    backup di quel nome in quel repository, e lo stesso nome può stare più
    volte nello stesso repository con tipi diversi, per esempio la posta, il
    OneDrive e il sito SharePoint personale della stessa persona.

    Elencali in Markdown, sotto il nome del repository come titolo di quinto
    livello («##### nome»), in un elenco puntato con un backup per riga, nella
    forma «nome, tipo: ultimo punto, da quanto fa». Un backup sta su una riga
    sola, senza sottopunti. Le righe con `non_aggiornato` (ultimo punto troppo
    vecchio, o nessuno) finiscono con «non aggiornato» in grassetto, quelle con
    `non_accessibile` (Nakivo non riesce ad accedere al backup) con «non
    accessibile» in grassetto.
    """
    logger.info(f"backup per nome {nome!r}, azienda {company_id}")
    with _traduci_errori():
        data = await ars_client.chiama("/api/nakivo/backup-status", {"company_id": company_id})
        return normalizza.per_nome(
            data,
            nome,
            soglia_ore=settings.SOGLIA_BACKUP_ORE,
        )


@mcp.tool(annotations=SOLA_LETTURA)
async def nakivo_backup_del_repository(company_id: int, repository: str) -> dict:
    """I backup contenuti in un repository, dato il suo nome.

    Legge soltanto: non esegue nessun backup. `repository` è il nome come
    compare in `nakivo_stato_backup` o come lo scrive l'utente, per esempio
    "nasbackup.opero.local"; basta anche una parte, se un solo repository la
    contiene. Se il nome non basta, l'errore dice quali repository ci sono.

    Ogni riga dice `piattaforma` e `tipo`, come in `nakivo_stato_backup`, e
    `backup_per_piattaforma` conta i backup per piattaforma. Le righe
    identiche, tipo compreso, arrivano unite in una sola, con `righe` che
    dice quante sono; `totale_backup` le conta tutte, una per una.

    Elenca i backup in Markdown, sotto il nome del repository come titolo di
    quinto livello («##### nome»), in un elenco puntato con un backup per
    riga, nella forma «nome, tipo: ultimo punto, da quanto fa». Un backup sta
    su una riga sola, senza sottopunti. Le righe con `non_aggiornato` (ultimo
    punto troppo vecchio, o nessuno) finiscono con «non aggiornato» in grassetto,
    quelle con `non_accessibile` con «non accessibile» in grassetto.

    La risposta dice anche `tipo` del repository, se è un federato, e il suo
    `stato`, se non è a posto. Se ha `membro_di`, il repository fa parte di un repository
    federato: i backup stanno nel federato, quindi è normale che qui non ce
    ne siano, e per vederli serve il federato. Se ha `membri`, è il federato,
    e quelli sono i repository di cui è fatto.

    Usalo solo per una domanda su quel repository. Se la domanda riguarda un
    backup per nome, usa `nakivo_backup_per_nome`: lo stesso nome esiste
    spesso anche in altri repository, e qui non li vedresti.
    """
    logger.info(f"backup del repository {repository!r}, azienda {company_id}")
    # ARS vuole l'id Nakivo del repository. Con un id che non e' dell'azienda
    # risponde "0 backup" invece di un errore: per questo l'id lo cerca il
    # server fra i repository dell'azienda, e non lo sceglie il modello.
    with _traduci_errori():
        elenco = await ars_client.chiama(
            "/api/nakivo/repositories/all", {"company_id": company_id}
        )
        trovato = normalizza.cerca_repository(elenco, repository)
        data = await ars_client.chiama(
            "/api/nakivo/repositories/backups",
            {"company_id": company_id, "repository_id": trovato["id"]},
        )
    return normalizza.backup(
        data,
        trovato["nome"],
        soglia_ore=settings.SOGLIA_BACKUP_ORE,
        repositories=elenco,
    )


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=settings.HOST,
        port=settings.PORT,
        path=settings.MCP_PATH,
        show_banner=False,
    )
