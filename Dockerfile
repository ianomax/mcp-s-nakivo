# syntax=docker/dockerfile:1.7
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# tini raccoglie i processi orfani; curl serve al healthcheck del compose.
RUN apt-get update && apt-get install -y --no-install-recommends tini curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dipendenze: requirements.txt e' il lock generato da requirements.in.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app /app/app

# Utente non privilegiato.
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser /app
USER appuser

EXPOSE 8010

ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["python","-m","app.main_server"]
