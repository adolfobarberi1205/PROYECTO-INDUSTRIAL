from pathlib import Path
from typing import BinaryIO
from uuid import uuid4
from hashlib import sha256
import re

from app.core.repository_parser import (
    obtener_inventario_repositorio,
    validar_repositorio_xml,
)


TAMANO_MAXIMO_BYTES = 500 * 1024 * 1024
TAMANO_BLOQUE_BYTES = 1024 * 1024


def guardar_repositorio_temporal(
    archivo_origen: BinaryIO,
    nombre_original: str | None,
    directorio_temporal: str | Path,
) -> dict:
    """
    Guarda un repositorio XML en una ubicación temporal.

    El archivo recibe un identificador único y no conserva
    el nombre original como nombre físico.
    """

    if not nombre_original:
        raise ValueError("El archivo no tiene un nombre válido.")

    if Path(nombre_original).suffix.lower() != ".xml":
        raise ValueError("Solamente se permiten archivos con extensión .xml.")

    directorio = Path(directorio_temporal)
    directorio.mkdir(parents=True, exist_ok=True)

    id_carga = uuid4().hex
    ruta_temporal = directorio / f"{id_carga}.xml"

    total_bytes = 0
    fingerprint = sha256()

    try:
        with ruta_temporal.open("wb") as archivo_destino:
            while True:
                bloque = archivo_origen.read(TAMANO_BLOQUE_BYTES)

                if not bloque:
                    break

                total_bytes += len(bloque)

                if total_bytes > TAMANO_MAXIMO_BYTES:
                    raise ValueError(
                        "El archivo supera el límite permitido de 500 MB."
                    )

                archivo_destino.write(bloque)
                fingerprint.update(bloque)

        validacion = validar_repositorio_xml(ruta_temporal)
        inventario = obtener_inventario_repositorio(ruta_temporal)

        return {
            "id_carga": id_carga,
            "ruta_temporal": ruta_temporal,
            "nombre_original": Path(nombre_original).name,
            "tamano_bytes": total_bytes,
            "huella_sha256": fingerprint.hexdigest(),
            "validacion": validacion,
            "inventario": inventario,
        }

    except Exception:
        ruta_temporal.unlink(missing_ok=True)
        raise

PATRON_ID_CARGA = re.compile(r"^[a-f0-9]{32}$")


def obtener_ruta_repositorio_temporal(
    id_carga: str,
    directorio_temporal: str | Path,
) -> Path:
    """
    Localiza un repositorio cargado temporalmente mediante
    su identificador único.
    """

    id_normalizado = id_carga.strip().lower()

    if not PATRON_ID_CARGA.fullmatch(id_normalizado):
        raise ValueError("El identificador de carga no es válido.")

    directorio = Path(directorio_temporal)
    ruta_temporal = directorio / f"{id_normalizado}.xml"

    if not ruta_temporal.exists():
        raise FileNotFoundError(
            "No se encontró el repositorio temporal solicitado."
        )

    if not ruta_temporal.is_file():
        raise ValueError(
            "La carga temporal no corresponde a un archivo."
        )

    return ruta_temporal
