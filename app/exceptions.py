# app/exceptions.py
"""Le eccezioni definite dal server, tutte figlie di `ErroreNakivo`.

Ognuna porta nel messaggio la frase da mostrare all'utente; il dettaglio
tecnico resta nei log. Dentro un tool, un'eccezione di questa famiglia arriva
al client come `ToolError` con la sua frase (`main_server._traduci_errori`):
un'eccezione nuova si definisce qui, come figlia di `ErroreNakivo`.

`ToolError` non sta qui: e' di FastMCP, che fa passare al client solo il
messaggio di quella classe.
"""


class ErroreNakivo(Exception):
    """La base: il messaggio e' la frase per l'utente."""


class ErroreArs(ErroreNakivo):
    """Un guasto nella chiamata ad ARS, gia' tradotto in una frase per l'utente."""


class RepositoryNonTrovato(ErroreNakivo):
    """Il nome non indica un repository dell'azienda: il messaggio dice quali ci sono."""
