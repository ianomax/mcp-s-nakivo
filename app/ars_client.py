# app/ars_client.py
"""Il ponte verso l'API Nakivo di ARS.

Un solo punto in cui si parla HTTP: i tool chiamano `chiama()` e ricevono il
contenuto di `data`, oppure un `ErroreArs` con la frase da far leggere al
modello.

La divisione e' deliberata: **al modello arrivano solo frasi che hanno senso
per l'utente**, il dettaglio tecnico (codice HTTP, corpo, percorso) resta nei
log. Il token non compare da nessuna parte -- ne' nei messaggi ne' nei log.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx2

from .exceptions import ErroreArs
from .settings import settings

logger = logging.getLogger("ARS")

# Timeout separati per fase: connettersi deve essere rapido, leggere no --
# dall'altra parte ARS fa login su Nakivo e interroga ogni repository.
TIMEOUT = httpx2.Timeout(
    connect=5.0, read=settings.ARS_TIMEOUT, write=10.0, pool=5.0
)

MSG_GENERICO = "Il servizio dei backup non è disponibile al momento."
MSG_NON_CONFIGURATO = "Il backup Nakivo non è configurato per questa azienda."
MSG_NAKIVO = "Nakivo non ha risposto correttamente: i dati dei backup non sono disponibili."
MSG_TROPPE = "Troppe richieste al servizio dei backup: riprova fra un minuto."
MSG_TIMEOUT = "Il servizio dei backup non risponde: la lettura da Nakivo sta impiegando troppo."

# ARS risponde 422 in due casi opposti: l'azienda non ha un controllo Nakivo
# (il caso normale per quasi tutte) oppure il corpo inviato e' sbagliato. Li
# distingue solo il messaggio: se ARS cambia quel testo si ricade sulla frase
# generica, non su un errore.
TESTO_NON_CONFIGURATO = "nakivo backup check not found"


_client: Optional[httpx2.AsyncClient] = None


def _prendi_client() -> httpx2.AsyncClient:
    """Un solo client per processo, creato dentro il loop che lo usera'."""
    global _client
    if _client is None:
        _client = httpx2.AsyncClient(
            base_url=settings.ARS_BASE_URL.rstrip("/"),
            timeout=TIMEOUT,
            headers={
                "Authorization": f"Bearer {settings.ARS_API_TOKEN}",
                "Content-Type": "application/json",
                # Senza questo ARS risponde con un redirect alla pagina di
                # login invece che con il JSON dell'errore.
                "Accept": "application/json",
            },
        )
    return _client


async def chiudi() -> None:
    """Chiude il client HTTP. Da chiamare allo spegnimento del server."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _busta(risposta: httpx2.Response) -> Optional[dict]:
    """Il JSON della risposta, o None se non e' un oggetto JSON.

    Serve a distinguere un errore di ARS (busta `success`/`message`/`data`)
    da un URL sbagliato, che torna come pagina HTML.
    """
    try:
        corpo = risposta.json()
    except Exception:
        return None
    return corpo if isinstance(corpo, dict) else None


async def chiama(percorso: str, corpo: dict[str, Any]) -> Any:
    """POST a un'API Nakivo di ARS; restituisce il contenuto di `data`.

    Solleva `ErroreArs` con la frase da mostrare; il perche' finisce nei log.
    """
    try:
        risposta = await _prendi_client().post(percorso, json=corpo)
    except httpx2.TimeoutException as e:
        logger.error(f"timeout su {percorso}: {type(e).__name__}")
        raise ErroreArs(MSG_TIMEOUT)
    except httpx2.HTTPError as e:
        logger.error(f"ARS irraggiungibile su {percorso}: {type(e).__name__}: {e}")
        raise ErroreArs(MSG_GENERICO)

    stato = risposta.status_code
    busta = _busta(risposta)
    messaggio = busta.get("message") if busta else None

    # Il limite di 60 richieste al minuto di ARS e' legato al token, quindi e'
    # condiviso da tutte le chat: puo' scattare senza che questa sia di troppo.
    if stato == 429:
        logger.warning(f"limite di richieste di ARS superato su {percorso}")
        raise ErroreArs(MSG_TROPPE)

    if busta is None:
        logger.error(
            f"risposta non JSON da {percorso}: HTTP {stato}, content-type "
            f"{risposta.headers.get('content-type')!r} -- percorso o URL sbagliato?"
        )
        raise ErroreArs(MSG_GENERICO)

    if stato == 401:
        logger.error(
            f"ARS ha rifiutato il token su {percorso} (401): configurazione da correggere"
        )
        raise ErroreArs(MSG_GENERICO)

    # Con la busta JSON il 404 significa "questa azienda non ha un controllo
    # Nakivo": un percorso inesistente sarebbe arrivato come pagina HTML,
    # gia' scartata sopra.
    if stato == 404:
        logger.info(f"nessun controllo Nakivo su {percorso}: {messaggio!r}")
        raise ErroreArs(MSG_NON_CONFIGURATO)

    if stato == 422:
        if TESTO_NON_CONFIGURATO in (messaggio or "").lower():
            logger.info(f"nessun controllo Nakivo su {percorso}: {messaggio!r}")
            raise ErroreArs(MSG_NON_CONFIGURATO)
        logger.error(
            f"ARS ha rifiutato il corpo su {percorso} (422): {messaggio!r} -- inviato {corpo}"
        )
        raise ErroreArs(MSG_GENERICO)

    if stato >= 500 or busta.get("success") is not True:
        logger.error(
            f"ARS ha risposto HTTP {stato} con success={busta.get('success')!r} "
            f"su {percorso}: {messaggio!r}"
        )
        raise ErroreArs(MSG_NAKIVO)

    return busta.get("data")
