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
from contextlib import asynccontextmanager
from typing import Any

import fastmcp
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from starlette.responses import JSONResponse

from . import ars_client, normalizza
from .ars_client import ErroreArs
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
        f"Ora locale: {normalizza.adesso().isoformat(timespec='minutes')}"
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


async def _chiama(percorso: str, corpo: dict[str, Any]) -> Any:
    """Chiama ARS e traduce il guasto in un errore del tool.

    `ToolError` e' l'unica eccezione il cui messaggio FastMCP lascia passare
    al client: e' li' che la frase pensata per l'utente diventa la risposta
    del tool.
    """
    try:
        return await ars_client.chiama(percorso, corpo)
    except ErroreArs as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=SOLA_LETTURA)
async def nakivo_stato_backup(company_id: int) -> dict:
    """Stato dei backup Nakivo dell'azienda: tutti i repository con i loro backup.

    È il tool giusto per la maggior parte delle domande sui backup, comprese
    quelle su un singolo backup o su un singolo repository.

    **Un backup con lo stesso nome sta spesso in più repository, con date
    molto diverse.** `nomi_in_piu_repository` dice quali nomi sono in questo
    caso e in quanti repository stanno: se ti chiedono di uno di quei nomi,
    cerca tutte le righe con quel nome e riportale **tutte**, ognuna col suo
    repository. Non fermarti alla prima che trovi.

    Ogni riga è già "l'ultimo backup" di quel nome in quel repository. Per un
    nome che sta in tre repository ci sono quindi tre ultimi backup, con tre
    date: chi chiede "quando è stato fatto l'ultimo backup di X" li vuole
    tutti e tre, anche quelli vecchi. Non sceglierne uno.

    Per ogni backup dice quando è stato creato l'ultimo punto di ripristino
    (`ultimo_punto`) e quanto tempo è passato (`ore_fa` e `da_quanto`, già
    calcolati: non rifare i conti con le date).

    Ogni riga porta il suo `repository`: cita sempre quale è, non prendere la
    data di un repository per un altro e non concludere che un backup è
    vecchio se in un altro repository ne esiste uno recente.

    I totali sono già contati (`totale_repository`, `totale_backup`, e uno
    per ogni repository): riporta quelli, non contare le righe a mano.

    I dati dicono solo quando è stato creato l'ultimo punto di ripristino:
    non dicono se il job è andato a buon fine, quindi non affermare che un
    backup è "riuscito" o "fallito".
    """
    logger.info(f"stato dei backup, azienda {company_id}")
    data = await _chiama("/api/nakivo/backup-status", {"company_id": company_id})
    return normalizza.stato(data)


@mcp.tool(annotations=SOLA_LETTURA)
async def nakivo_elenco_repository(company_id: int) -> dict:
    """Elenco dei repository Nakivo dell'azienda: solo id e nome.

    Serve quando ti interessa un repository soltanto: prendi il suo `id` qui
    e passalo a `nakivo_backup_del_repository`. Per una panoramica completa
    usa invece `nakivo_stato_backup`.
    """
    logger.info(f"elenco dei repository, azienda {company_id}")
    data = await _chiama("/api/nakivo/repositories/all", {"company_id": company_id})
    return normalizza.repository(data)


@mcp.tool(annotations=SOLA_LETTURA)
async def nakivo_backup_del_repository(company_id: int, repository_id: int) -> dict:
    """I backup contenuti in un repository, dato il suo id.

    Legge soltanto: non esegue nessun backup. L'id del repository si prende
    da `nakivo_elenco_repository` o da `nakivo_stato_backup`.

    Usalo solo per una domanda su quel repository. Se la domanda riguarda un
    backup per nome, usa `nakivo_stato_backup`: lo stesso nome esiste spesso
    anche in altri repository, e qui non li vedresti.
    """
    logger.info(f"backup del repository {repository_id}, azienda {company_id}")
    data = await _chiama(
        "/api/nakivo/repositories/backups",
        {"company_id": company_id, "repository_id": repository_id},
    )
    return normalizza.backup(data, repository_id)


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=settings.HOST,
        port=settings.PORT,
        path=settings.MCP_PATH,
        show_banner=False,
    )
