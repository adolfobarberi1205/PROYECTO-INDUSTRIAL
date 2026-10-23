FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# The legacy analyzer imports tkinter, but never opens a window in the API.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libtk8.6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 etl \
    && useradd --uid 10001 --gid etl --create-home etl

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=etl:etl backend/app ./app
COPY --chown=etl:etl legacy/reconstructor_pentaho.py /app/legacy/reconstructor_pentaho.py
RUN mkdir -p /app/archivos_temporales /app/resultados_temporales \
    && chown etl:etl /app/archivos_temporales /app/resultados_temporales

USER etl
# Fail at build time if the existing analyzer cannot load in Linux.
RUN python -c "from app.core.legacy_adapter import cargar_motor_legacy; cargar_motor_legacy()"

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--root-path", "/api"]
