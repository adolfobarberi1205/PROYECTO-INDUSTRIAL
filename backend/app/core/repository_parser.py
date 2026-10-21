from pathlib import Path

import xml.etree.ElementTree as ET


def validar_repositorio_xml(ruta_xml: str | Path) -> dict:
    """
    Comprueba que el archivo existe, es XML y corresponde
    a una exportación de repositorio Pentaho.
    """

    ruta = Path(ruta_xml)

    if not ruta.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {ruta}")

    if not ruta.is_file():
        raise ValueError(f"La ruta no corresponde a un archivo: {ruta}")

    if ruta.suffix.lower() != ".xml":
        raise ValueError("El archivo debe tener extensión .xml")

    try:
        evento, elemento = next(
            ET.iterparse(ruta, events=("start",))
        )
    except ET.ParseError as error:
        raise ValueError(f"El archivo XML no es válido: {error}") from error
    except StopIteration as error:
        raise ValueError("El archivo XML está vacío") from error

    if elemento.tag != "repository":
        raise ValueError(
            f"El XML no parece ser un repositorio Pentaho. "
            f"Raíz encontrada: {elemento.tag}"
        )

    return {
        "archivo": ruta.name,
        "raiz": elemento.tag,
        "tamano_bytes": ruta.stat().st_size,
        "valido": True,
    }

def obtener_inventario_repositorio(ruta_xml: str | Path) -> dict:
    """
    Recorre el repositorio Pentaho sin cargar todo el XML
    simultáneamente en memoria.

    Devuelve la cantidad de Jobs, transformaciones y carpetas.
    """

    ruta = Path(ruta_xml)

    # Reutilizamos la validación que ya probamos.
    validar_repositorio_xml(ruta)

    cantidad_jobs = 0
    cantidad_transformaciones = 0
    carpetas: set[str] = set()

    try:
        for evento, elemento in ET.iterparse(ruta, events=("end",)):
            if elemento.tag == "transformation":
                cantidad_transformaciones += 1

                carpeta = elemento.findtext("./info/directory")
                if carpeta:
                    carpetas.add(carpeta.strip())

                elemento.clear()

            elif elemento.tag == "job":
                cantidad_jobs += 1

                carpeta = elemento.findtext("./directory")
                if not carpeta:
                    carpeta = elemento.findtext("./info/directory")

                if carpeta:
                    carpetas.add(carpeta.strip())

                elemento.clear()

    except ET.ParseError as error:
        raise ValueError(
            f"No fue posible recorrer el repositorio XML: {error}"
        ) from error

    return {
        "jobs": cantidad_jobs,
        "transformaciones": cantidad_transformaciones,
        "carpetas": sorted(carpetas),
        "cantidad_carpetas": len(carpetas),
    }

def obtener_jobs_por_carpeta(
    ruta_xml: str | Path,
    carpeta: str,
) -> dict:
    """
    Obtiene los Jobs que pertenecen a una carpeta específica
    del repositorio Pentaho.
    """

    ruta = Path(ruta_xml)
    validar_repositorio_xml(ruta)

    carpeta_buscada = carpeta.strip()
    jobs_encontrados: list[str] = []

    try:
        for evento, elemento in ET.iterparse(ruta, events=("end",)):
            if elemento.tag != "job":
                continue

            carpeta_job = (
                elemento.findtext("./directory")
                or elemento.findtext("./info/directory")
                or ""
            ).strip()

            if carpeta_job == carpeta_buscada:
                nombre_job = (
                    elemento.findtext("./name")
                    or elemento.findtext("./info/name")
                    or ""
                ).strip()

                if nombre_job:
                    jobs_encontrados.append(nombre_job)

            elemento.clear()

    except ET.ParseError as error:
        raise ValueError(
            f"No fue posible recorrer el repositorio XML: {error}"
        ) from error

    jobs_ordenados = sorted(set(jobs_encontrados))

    return {
        "carpeta": carpeta_buscada,
        "jobs": jobs_ordenados,
        "cantidad_jobs": len(jobs_ordenados),
    }

def buscar_job_principal(
    ruta_xml: str | Path,
    carpeta: str,
    nombre_job: str,
) -> dict:
    """
    Busca un Job específico dentro de una carpeta del repositorio.
    Devuelve sus datos básicos sin reconstruir todavía el proceso.
    """

    ruta = Path(ruta_xml)
    validar_repositorio_xml(ruta)

    carpeta_buscada = carpeta.strip()
    nombre_buscado = nombre_job.strip()

    try:
        for _, elemento in ET.iterparse(ruta, events=("end",)):
            if elemento.tag != "job":
                continue

            carpeta_job = (
                elemento.findtext("./directory")
                or elemento.findtext("./info/directory")
                or ""
            ).strip()

            nombre_encontrado = (
                elemento.findtext("./name")
                or elemento.findtext("./info/name")
                or ""
            ).strip()

            if (
                carpeta_job == carpeta_buscada
                and nombre_encontrado == nombre_buscado
            ):
                entradas = elemento.findall("./entries/entry")
                saltos = elemento.findall("./hops/hop")

                resultado = {
                    "nombre": nombre_encontrado,
                    "carpeta": carpeta_job,
                    "cantidad_entradas": len(entradas),
                    "cantidad_saltos": len(saltos),
                    "encontrado": True,
                }

                elemento.clear()
                return resultado

            elemento.clear()

    except ET.ParseError as error:
        raise ValueError(
            f"No fue posible recorrer el repositorio XML: {error}"
        ) from error

    raise ValueError(
        f"No se encontró el Job '{nombre_buscado}' "
        f"en la carpeta '{carpeta_buscada}'."
    )
def obtener_estructura_job(
    ruta_xml: str | Path,
    carpeta: str,
    nombre_job: str,
) -> dict:
    """
    Obtiene las entradas y los saltos de un Job específico.
    Esta información permitirá reconstruir su diagrama.
    """

    ruta = Path(ruta_xml)
    validar_repositorio_xml(ruta)

    carpeta_buscada = carpeta.strip()
    nombre_buscado = nombre_job.strip()

    try:
        for _, elemento in ET.iterparse(ruta, events=("end",)):
            if elemento.tag != "job":
                continue

            carpeta_job = (
                elemento.findtext("./directory")
                or elemento.findtext("./info/directory")
                or ""
            ).strip()

            nombre_encontrado = (
                elemento.findtext("./name")
                or elemento.findtext("./info/name")
                or ""
            ).strip()

            if (
                carpeta_job == carpeta_buscada
                and nombre_encontrado == nombre_buscado
            ):
                entradas = []

                for entrada in elemento.findall("./entries/entry"):
                    nombre_entrada = (
                        entrada.findtext("./name") or ""
                    ).strip()

                    tipo_entrada = (
                        entrada.findtext("./type") or ""
                    ).strip()

                    xloc = (
                        entrada.findtext("./xloc")
                        or entrada.findtext("./GUI/xloc")
                        or ""
                    ).strip()

                    yloc = (
                        entrada.findtext("./yloc")
                        or entrada.findtext("./GUI/yloc")
                        or ""
                    ).strip()

                    entradas.append(
                        {
                            "nombre": nombre_entrada,
                            "tipo": tipo_entrada,
                            "x": int(xloc) if xloc.isdigit() else None,
                            "y": int(yloc) if yloc.isdigit() else None,
                        }
                    )

                saltos = []

                for salto in elemento.findall("./hops/hop"):
                    saltos.append(
                        {
                            "origen": (
                                salto.findtext("./from") or ""
                            ).strip(),
                            "destino": (
                                salto.findtext("./to") or ""
                            ).strip(),
                            "habilitado": (
                                salto.findtext("./enabled") or ""
                            ).strip() == "Y",
                            "evaluacion": (
                                salto.findtext("./evaluation") or ""
                            ).strip(),
                            "incondicional": (
                                salto.findtext("./unconditional") or ""
                            ).strip() == "Y",
                        }
                    )

                resultado = {
                    "nombre": nombre_encontrado,
                    "carpeta": carpeta_job,
                    "entradas": entradas,
                    "saltos": saltos,
                    "cantidad_entradas": len(entradas),
                    "cantidad_saltos": len(saltos),
                }

                elemento.clear()
                return resultado

            elemento.clear()

    except ET.ParseError as error:
        raise ValueError(
            f"No fue posible recorrer el repositorio XML: {error}"
        ) from error

    raise ValueError(
        f"No se encontró el Job '{nombre_buscado}' "
        f"en la carpeta '{carpeta_buscada}'."
    )