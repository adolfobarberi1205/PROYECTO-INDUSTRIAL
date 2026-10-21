from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from app.core.legacy_adapter import (
    cargar_motor_legacy,
)
from app.core.metadata_sanitizer import (
    sanitizar_metadatos,
)
from app.services.diagram_export_service import (
    construir_diagramas_proceso,
)
from app.services.lineage_export_service import (
    exportar_linajes_variables,
)
from app.services.warehouse_table_analysis_service import (
    analyze_warehouse_tables,
)
from app.services.fact_dimension_analysis_service import (
    analyze_fact_dimension_participation,
)
from app.services.dimension_construction_analysis_service import (
    analyze_dimension_construction,
)


def calcular_huella_repositorio(
    ruta_xml: str | Path,
    tamano_bloque: int = 1024 * 1024,
) -> str:
    """Calcula una huella SHA-256 estable a partir del XML completo."""

    ruta = Path(ruta_xml)
    huella = sha256()

    with ruta.open("rb") as archivo:
        while bloque := archivo.read(tamano_bloque):
            huella.update(bloque)

    return huella.hexdigest()


def analizar_proceso_etl(
    ruta_xml: str | Path,
    carpeta: str,
    nombre_job: str,
    ruta_kettle: str | Path | None = None,
    nombre_repositorio: str | None = None,
    repositorio_id: int | None = None,
    motor_preparado: Any | None = None,
    inventario_preparado: dict[str, Any] | None = None,
    huella_preparada: str | None = None,
) -> dict[str, Any]:
    """
    Analiza y reconstruye un proceso ETL seleccionado.

    También exporta los diagramas internos de todos los
    procesos alcanzables antes de sanitizar el resultado.
    """

    ruta = Path(ruta_xml)

    if not ruta.exists():
        raise FileNotFoundError(
            f"No se encontró el repositorio XML: {ruta}"
        )

    if not ruta.is_file():
        raise ValueError(
            f"La ruta no corresponde a un archivo: {ruta}"
        )

    inicio = perf_counter()
    huella_repositorio = (
        huella_preparada or calcular_huella_repositorio(ruta)
    )

    motor = motor_preparado or cargar_motor_legacy()

    if inventario_preparado is None:
        _, inventario = motor.cargar_xml_repositorio(ruta)
    else:
        inventario = inventario_preparado

    carpeta_normalizada = motor.normalizar_ruta(
        carpeta
    ).casefold()

    nombre_normalizado = (
        nombre_job.strip().casefold()
    )

    coincidencias = [
        job
        for job in inventario.get(
            "jobs",
            {},
        ).values()
        if job.get(
            "nombre",
            "",
        ).strip().casefold()
        == nombre_normalizado
        and motor.normalizar_ruta(
            job.get(
                "ruta",
                "",
            )
        ).casefold()
        == carpeta_normalizada
    ]

    if not coincidencias:
        raise ValueError(
            f"No se encontró el Job '{nombre_job}' "
            f"en la carpeta '{carpeta}'."
        )

    if len(coincidencias) > 1:
        raise ValueError(
            f"Se encontraron varios Jobs llamados "
            f"'{nombre_job}' en la carpeta "
            f"'{carpeta}'."
        )

    job_principal = coincidencias[0]

    resultado_motor = motor.reconstruir_proceso(
        job_principal_id=job_principal["id"],
        inventario=inventario,
    )

    propiedades_kettle: dict[str, dict[str, Any]] = {}
    if ruta_kettle is not None:
        ruta_propiedades = Path(ruta_kettle)
        if ruta_propiedades.exists() and ruta_propiedades.is_file():
            propiedades_kettle = motor.cargar_kettle_properties(
                ruta_propiedades
            )

    exportacion_linaje = exportar_linajes_variables(
        motor=motor,
        inventario=inventario,
        resultado=resultado_motor,
        propiedades_kettle=propiedades_kettle,
    )

    resultado_motor["linaje_variables"] = exportacion_linaje[
        "linajes"
    ]
    resultado_motor["errores_linaje_variables"] = exportacion_linaje[
        "errores"
    ]

    tablas_dwh = analyze_warehouse_tables(
        inventory=inventario,
        result=resultado_motor,
        kettle_properties=propiedades_kettle,
    )

    resultado_motor["tablas_dwh"] = tablas_dwh

    relaciones_fact_dim = analyze_fact_dimension_participation(
        inventory=inventario,
        result=resultado_motor,
        warehouse_tables=tablas_dwh,
    )

    resultado_motor["relaciones_fact_dim"] = relaciones_fact_dim

    construccion_dimensiones = analyze_dimension_construction(
        inventory=inventario,
        result=resultado_motor,
        warehouse_tables=tablas_dwh,
    )

    resultado_motor["construccion_dimensiones"] = construccion_dimensiones

    diagramas = construir_diagramas_proceso(
        inventario=inventario,
        resultado=resultado_motor,
    )

    resultado_motor["diagramas"] = diagramas

    resultado_motor[
        "diagrama_principal_id"
    ] = job_principal["id"]

    nodos_resultado = resultado_motor.get(
        "nodos",
        {},
    )

    if isinstance(
        nodos_resultado,
        dict,
    ):
        nodos = list(
            nodos_resultado.values()
        )
    else:
        nodos = list(
            nodos_resultado
        )

    resumen_motor = resultado_motor.get(
        "proceso_reconstruido",
        {},
    )

    jobs_alcanzables = sum(
        1
        for nodo in nodos
        if nodo.get("tipo") == "JOB"
    )

    transformaciones_alcanzables = sum(
        1
        for nodo in nodos
        if nodo.get("tipo")
        == "TRANSFORMATION"
    )

    duracion = perf_counter() - inicio

    respuesta = {
        "analisis": {
            "archivo": ruta.name,
            "huella_sha256": huella_repositorio,
            "nombre_repositorio": (
                nombre_repositorio.strip()
                if nombre_repositorio
                else Path(ruta.name).stem
            ),
            "repositorio_id": repositorio_id,
            "carpeta": job_principal.get(
                "ruta",
                carpeta,
            ),
            "job_principal": (
                job_principal.get(
                    "nombre",
                    nombre_job,
                )
            ),
            "fecha_analisis": datetime.now(
                timezone.utc
            ).isoformat(),
            "duracion_segundos": round(
                duracion,
                2,
            ),
            "estado": "completado",
        },
        "resumen": {
            "jobs": jobs_alcanzables,
            "transformaciones": (
                transformaciones_alcanzables
            ),
            "procesos": len(nodos),
            "relaciones": len(
                resultado_motor.get(
                    "relaciones",
                    [],
                )
            ),
            "diagramas": len(
                diagramas
            ),
            "linajes_variables": exportacion_linaje["total"],
            "tablas_dim": tablas_dwh["total_dimensiones"],
            "tablas_fact": tablas_dwh["total_hechos"],
            "relaciones_fact_dim": relaciones_fact_dim[
                "total_relaciones"
            ],
            "dimensiones_analizadas": construccion_dimensiones[
                "total_dimensiones"
            ],
            "parametros": resumen_motor.get(
                "total_parametros",
                0,
            ),
            "variables": resumen_motor.get(
                "total_variables",
                0,
            ),
            "referencias_no_resueltas": len(
                resultado_motor.get(
                    "referencias_no_resueltas",
                    [],
                )
            ),
            "referencias_ambiguas": len(
                resultado_motor.get(
                    "referencias_ambiguas",
                    [],
                )
            ),
            "referencias_dinamicas": len(
                resultado_motor.get(
                    "referencias_dinamicas",
                    [],
                )
            ),
        },
        "resultado": resultado_motor,
    }

    sanitizacion = sanitizar_metadatos(
        respuesta
    )

    respuesta_limpia = sanitizacion[
        "contenido"
    ]

    respuesta_limpia["seguridad"] = {
        "sanitizado": True,
        "total_redacciones": sanitizacion[
            "total_redacciones"
        ],
        "redacciones_por_categoria": (
            sanitizacion["redacciones"]
        ),
    }

    return respuesta_limpia


def analizar_lote_procesos_etl(
    ruta_xml: str | Path,
    procesos: list[dict[str, Any]],
    nombre_repositorio: str,
    repositorio_id: int | None = None,
    ruta_kettle: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Analiza varios Jobs reutilizando una sola lectura del repositorio."""

    ruta = Path(ruta_xml)

    if not ruta.exists() or not ruta.is_file():
        raise FileNotFoundError(
            f"No se encontro el repositorio XML: {ruta}"
        )

    motor = cargar_motor_legacy()
    _, inventario = motor.cargar_xml_repositorio(ruta)
    fingerprint = calcular_huella_repositorio(ruta)
    results: list[dict[str, Any]] = []

    for index, process in enumerate(procesos):
        try:
            analysis = analizar_proceso_etl(
                ruta_xml=ruta,
                carpeta=str(process["carpeta"]),
                nombre_job=str(process["nombre_job"]),
                ruta_kettle=ruta_kettle,
                nombre_repositorio=nombre_repositorio,
                repositorio_id=repositorio_id,
                motor_preparado=motor,
                inventario_preparado=inventario,
                huella_preparada=fingerprint,
            )
            results.append(
                {
                    "indice": index,
                    "estado": "completado",
                    "nombre_proceso": process["nombre_proceso"],
                    "descripcion": process.get("descripcion"),
                    "carpeta": process["carpeta"],
                    "nombre_job": process["nombre_job"],
                    "analisis": analysis,
                }
            )
        except Exception as error:
            results.append(
                {
                    "indice": index,
                    "estado": "fallido",
                    "nombre_proceso": process.get("nombre_proceso", ""),
                    "descripcion": process.get("descripcion"),
                    "carpeta": process.get("carpeta", ""),
                    "nombre_job": process.get("nombre_job", ""),
                    "error": str(error),
                }
            )

    return results
