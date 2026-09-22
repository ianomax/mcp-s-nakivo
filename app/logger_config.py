import logging
import sys
from pythonjsonlogger.json import JsonFormatter


def setup_logging():
    """
    Configura il logger per stampare in formato JSON su stdout.

    """
    logger = logging.getLogger()

    # Controllo di sicurezza: se ci sono già handler, non se ne aggiungono
    # altri, per evitare che i log vengano stampati doppi.
    if logger.handlers:
        return

    log_handler = logging.StreamHandler(sys.stdout)

    formatter = JsonFormatter(
        '%(asctime)s %(levelname)s %(name)s %(message)s'
    )

    log_handler.setFormatter(formatter)
    logger.addHandler(log_handler)

    logger.setLevel(logging.INFO)
