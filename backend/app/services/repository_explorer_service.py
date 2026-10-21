from functools import lru_cache
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


def _nombre_local(etiqueta: str) -> str:
    """
    Elimina posibles namespaces de una etiqueta XML.
    """

    return etiqueta.rsplit("}", 1)[-1].lower()


def _buscar_hijo_directo(
    elemento: ET.Element | None,
    nombre: str,
) -> ET.Element | None:
    if elemento is None:
        return None

    nombre_normalizado = nombre.lower()

    for hijo in elemento:
        if _nombre_local(hijo.tag) == nombre_normalizado:
            return hijo

    return None


def _obtener_texto_directo(
    elemento: ET.Element | None,
    nombre: str,
) -> str:
    hijo = _buscar_hijo_directo(
        elemento=elemento,
        nombre=nombre,
    )

    if hijo is None or hijo.text is None:
        return ""

    return hijo.text.strip()


def _normalizar_carpeta(carpeta: str | None) -> str:
    if not carpeta:
        return "/"

    carpeta_normalizada = carpeta.strip().replace("\\", "/")

    if not carpeta_normalizada.startswith("/"):
        carpeta_normalizada = f"/{carpeta_normalizada}"

    while "//" in carpeta_normalizada:
        carpeta_normalizada = carpeta_normalizada.replace(
            "//",
            "/",
        )

    if (
        len(carpeta_normalizada) > 1
        and carpeta_normalizada.endswith("/")
    ):
        carpeta_normalizada = carpeta_normalizada.rstrip("/")

    return carpeta_normalizada


def _agregar_carpetas_padre(
    carpeta: str,
    carpetas: set[str],
) -> None:
    """
    Agrega la carpeta y todas sus carpetas superiores.

    Ejemplo:
    /MODELOS/TRANSACCIONES

    Agrega:
    /MODELOS
    /MODELOS/TRANSACCIONES
    """

    carpeta_normalizada = _normalizar_carpeta(carpeta)

    if carpeta_normalizada == "/":
        return

    partes = [
        parte
        for parte in carpeta_normalizada.split("/")
        if parte
    ]

    ruta_actual = ""

    for parte in partes:
        ruta_actual = f"{ruta_actual}/{parte}"
        carpetas.add(ruta_actual)


@lru_cache(maxsize=8)
def _construir_indice_repositorio(
    ruta_xml_texto: str,
) -> dict[str, Any]:
    """
    Recorre el XML y construye un índice de carpetas,
    Jobs y transformaciones.
    """

    ruta_xml = Path(ruta_xml_texto)

    if not ruta_xml.exists():
        raise FileNotFoundError(
            "No se encontró el repositorio temporal."
        )

    carpetas: set[str] = set()
    jobs: list[dict[str, str]] = []
    transformaciones: list[dict[str, str]] = []

    try:
        contexto = ET.iterparse(
            ruta_xml,
            events=("end",),
        )

        for _, elemento in contexto:
            tipo_elemento = _nombre_local(
                elemento.tag
            )

            if tipo_elemento == "job":
                nombre = _obtener_texto_directo(
                    elemento=elemento,
                    nombre="name",
                )

                carpeta = _obtener_texto_directo(
                    elemento=elemento,
                    nombre="directory",
                )

                if nombre:
                    carpeta_normalizada = (
                        _normalizar_carpeta(carpeta)
                    )

                    _agregar_carpetas_padre(
                        carpeta=carpeta_normalizada,
                        carpetas=carpetas,
                    )

                    jobs.append(
                        {
                            "nombre": nombre,
                            "carpeta": carpeta_normalizada,
                            "tipo": "job",
                        }
                    )

                # Limpiar solamente después de leer el Job.
                elemento.clear()

            elif tipo_elemento == "transformation":
                informacion = _buscar_hijo_directo(
                    elemento=elemento,
                    nombre="info",
                )

                nombre = _obtener_texto_directo(
                    elemento=informacion,
                    nombre="name",
                )

                carpeta = _obtener_texto_directo(
                    elemento=informacion,
                    nombre="directory",
                )

                if not nombre:
                    nombre = _obtener_texto_directo(
                        elemento=elemento,
                        nombre="name",
                    )

                if not carpeta:
                    carpeta = _obtener_texto_directo(
                        elemento=elemento,
                        nombre="directory",
                    )

                if nombre:
                    carpeta_normalizada = (
                        _normalizar_carpeta(carpeta)
                    )

                    _agregar_carpetas_padre(
                        carpeta=carpeta_normalizada,
                        carpetas=carpetas,
                    )

                    transformaciones.append(
                        {
                            "nombre": nombre,
                            "carpeta": carpeta_normalizada,
                            "tipo": "transformacion",
                        }
                    )

                # Limpiar solamente después de leer
                # la transformación completa.
                elemento.clear()

    except ET.ParseError as error:
        raise ValueError(
            "El repositorio XML no tiene una estructura válida."
        ) from error

    jobs.sort(
        key=lambda item: (
            item["carpeta"].lower(),
            item["nombre"].lower(),
        )
    )

    transformaciones.sort(
        key=lambda item: (
            item["carpeta"].lower(),
            item["nombre"].lower(),
        )
    )

    return {
        "carpetas": sorted(
            carpetas,
            key=lambda carpeta: carpeta.lower(),
        ),
        "jobs": jobs,
        "transformaciones": transformaciones,
    }


def obtener_carpetas_repositorio(
    ruta_xml: str | Path,
) -> list[str]:
    indice = _construir_indice_repositorio(
        str(Path(ruta_xml).resolve())
    )

    return list(indice["carpetas"])


def obtener_contenido_carpeta(
    ruta_xml: str | Path,
    carpeta: str,
) -> dict[str, Any]:
    carpeta_normalizada = _normalizar_carpeta(carpeta)

    indice = _construir_indice_repositorio(
        str(Path(ruta_xml).resolve())
    )

    carpetas_disponibles: list[str] = indice["carpetas"]

    if (
        carpeta_normalizada != "/"
        and carpeta_normalizada not in carpetas_disponibles
    ):
        raise ValueError(
            "La carpeta seleccionada no existe "
            "en el repositorio."
        )

    jobs = [
        item
        for item in indice["jobs"]
        if item["carpeta"] == carpeta_normalizada
    ]

    transformaciones = [
        item
        for item in indice["transformaciones"]
        if item["carpeta"] == carpeta_normalizada
    ]

    return {
        "carpeta": carpeta_normalizada,
        "total_procesos": (
            len(jobs) + len(transformaciones)
        ),
        "total_jobs": len(jobs),
        "total_transformaciones": len(
            transformaciones
        ),
        "jobs": jobs,
        "transformaciones": transformaciones,
    }