import re
from pathlib import Path
from typing import Any

from fastapi import UploadFile


PATRON_ID_CARGA = re.compile(r"^[a-f0-9]{32}$")
TAMANO_BLOQUE = 1024 * 1024


def obtener_ruta_kettle_temporal(
    id_carga: str,
    directorio_temporal: str | Path,
) -> Path | None:
    """Devuelve el kettle.properties asociado a una carga, si existe."""

    id_normalizado = id_carga.strip().lower()

    if not PATRON_ID_CARGA.fullmatch(id_normalizado):
        raise ValueError(
            "El identificador de carga no es válido."
        )

    ruta_kettle = (
        Path(directorio_temporal)
        / "complementos"
        / id_normalizado
        / "kettle.properties"
    )

    if not ruta_kettle.exists():
        return None

    if not ruta_kettle.is_file():
        raise ValueError(
            "La configuración Kettle temporal no corresponde a un archivo."
        )

    return ruta_kettle


def contar_variables_kettle(
    ruta_kettle: str | Path,
) -> int:
    """
    Cuenta las variables únicas definidas en kettle.properties.

    Los valores no se devuelven ni se exponen en la respuesta.
    """

    variables: set[str] = set()

    with Path(ruta_kettle).open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as archivo:
        for linea_original in archivo:
            linea = linea_original.strip()

            if not linea:
                continue

            if linea.startswith("#") or linea.startswith("!"):
                continue

            if "=" in linea:
                nombre_variable = linea.split("=", 1)[0].strip()
            elif ":" in linea:
                nombre_variable = linea.split(":", 1)[0].strip()
            else:
                continue

            if nombre_variable:
                variables.add(nombre_variable)

    return len(variables)


async def guardar_upload_file(
    archivo: UploadFile,
    destino: Path,
) -> int:
    """
    Guarda un UploadFile por bloques y devuelve su tamaño.
    """

    destino.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    total_bytes = 0

    await archivo.seek(0)

    with destino.open("wb") as archivo_destino:
        while True:
            bloque = await archivo.read(TAMANO_BLOQUE)

            if not bloque:
                break

            archivo_destino.write(bloque)
            total_bytes += len(bloque)

    await archivo.close()

    return total_bytes


async def guardar_archivos_adicionales(
    id_carga: str,
    directorio_temporal: str | Path,
    kettle: UploadFile | None = None,
    scripts: list[UploadFile] | None = None,
) -> dict[str, Any]:
    """
    Guarda kettle.properties y los scripts asociados
    a una carga de repositorio.
    """

    id_normalizado = id_carga.strip().lower()

    if not PATRON_ID_CARGA.fullmatch(id_normalizado):
        raise ValueError(
            "El identificador de carga no es válido."
        )

    directorio_base = (
        Path(directorio_temporal)
        / "complementos"
        / id_normalizado
    )

    directorio_base.mkdir(
        parents=True,
        exist_ok=True,
    )

    resultado: dict[str, Any] = {
        "kettle_cargado": False,
        "variables_encontradas": 0,
        "scripts_cargados": 0,
    }

    if kettle is not None:
        nombre_kettle = (
            kettle.filename or "kettle.properties"
        )

        extension = Path(nombre_kettle).suffix.lower()

        if extension not in {".properties", ".txt"}:
            raise ValueError(
                "El archivo Kettle debe ser .properties o .txt."
            )

        ruta_kettle = (
            directorio_base
            / "kettle.properties"
        )

        await guardar_upload_file(
            archivo=kettle,
            destino=ruta_kettle,
        )

        resultado["kettle_cargado"] = True
        resultado["variables_encontradas"] = (
            contar_variables_kettle(ruta_kettle)
        )

    scripts_recibidos = scripts or []

    if scripts_recibidos:
        directorio_scripts = (
            directorio_base
            / "scripts"
        )

        directorio_scripts.mkdir(
            parents=True,
            exist_ok=True,
        )

        for indice, script in enumerate(
            scripts_recibidos,
            start=1,
        ):
            nombre_original = (
                script.filename
                or f"script_{indice}.sh"
            )

            nombre_seguro = Path(nombre_original).name

            if not nombre_seguro.lower().endswith(".sh"):
                raise ValueError(
                    "Todos los scripts deben tener extensión .sh."
                )

            ruta_script = (
                directorio_scripts
                / f"{indice:03d}_{nombre_seguro}"
            )

            await guardar_upload_file(
                archivo=script,
                destino=ruta_script,
            )

        resultado["scripts_cargados"] = len(
            scripts_recibidos
        )

    return resultado
