# app/settings.py
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # L'API di ARS che esegue gli script verso Nakivo.
    ARS_BASE_URL: str

    # Token Sanctum per chiamare ARS
    ARS_API_TOKEN: str

    # ARS fa login su Nakivo e legge ogni repository, con 60 s di timeout per
    # ogni chiamata.
    ARS_TIMEOUT: float = 120.0

    # Segreto condiviso con mcp-c-ars. Senza, chiunque raggiunga questa porta
    # legge i backup di qualunque azienda passando un company_id.
    MCP_API_KEY: str = Field(min_length=8)

    # Un backup il cui ultimo punto di ripristino e' piu' vecchio di tante ore
    # e' segnato come non aggiornato.
    SOGLIA_BACKUP_ORE: float = Field(default=24.0, gt=0)

    HOST: str = "0.0.0.0"
    PORT: int = 8010
    MCP_PATH: str = "/mcp"

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'
    )


settings = Settings()
