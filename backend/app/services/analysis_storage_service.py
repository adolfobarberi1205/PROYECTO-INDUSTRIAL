import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4


PATRON_ID_ANALISIS = re.compile(r"^[a-f0-9]{32}$")


def guardar_resultado_analisis(
    resultado: dict[str, Any],
    directorio_resultados: str | Path,
) -> dict[str, Any]:
    """
    Guarda temporalmente el resultado sanitizado de un análisis.
    """

    directorio = Path(directorio_resultados)
    directorio.mkdir(parents=True, exist_ok=True)

    id_analisis = uuid4().hex
    ruta_resultado = directorio / f"{id_analisis}.json"

    try:
        with ruta_resultado.open(
            "w",
            encoding="utf-8",
        ) as archivo:
            json.dump(
                resultado,
                archivo,
                ensure_ascii=False,
                indent=2,
            )

    except Exception:
        ruta_resultado.unlink(missing_ok=True)
        raise

    return {
        "id_analisis": id_analisis,
        "ruta_resultado": ruta_resultado,
        "tamano_bytes": ruta_resultado.stat().st_size,
    }


def obtener_resultado_analisis(
    id_analisis: str,
    directorio_resultados: str | Path,
) -> dict[str, Any]:
    """
    Recupera un resultado temporal mediante su identificador.
    """

    id_normalizado = id_analisis.strip().lower()

    if not PATRON_ID_ANALISIS.fullmatch(id_normalizado):
        raise ValueError(
            "El identificador del análisis no es válido."
        )

    ruta_resultado = (
        Path(directorio_resultados)
        / f"{id_normalizado}.json"
    )

    if not ruta_resultado.exists():
        raise FileNotFoundError(
            "No se encontró el análisis solicitado."
        )

    with ruta_resultado.open(
        "r",
        encoding="utf-8",
    ) as archivo:
        return json.load(archivo)