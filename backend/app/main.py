from pathlib import Path
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy.orm import Session

from app.schemas.analysis_schema import (
    SolicitudAnalisis,
    SolicitudAnalisisLote,
)
from app.schemas.repository_schema import SolicitudRenombrarRepositorio
from app.schemas.documentation_schema import (
    SolicitudDocumentacionLote,
    SolicitudActualizacionProceso,
    SolicitudDocumentacion,
)
from app.db.session import get_database_session
from app.core.repository_parser import obtener_jobs_por_carpeta
from app.services.analysis_service import (
    analizar_lote_procesos_etl,
    analizar_proceso_etl,
)
from app.services.analysis_storage_service import (
    guardar_resultado_analisis,
    obtener_resultado_analisis,
)
from app.services.temporary_repository_service import (
    guardar_repositorio_temporal,
    obtener_ruta_repositorio_temporal,
)
from app.services.additional_files_service import (
    guardar_archivos_adicionales,
    obtener_ruta_kettle_temporal,
)
from app.services.repository_explorer_service import (
    obtener_carpetas_repositorio,
    obtener_contenido_carpeta,
)
from app.services.lineage_catalog_service import (
    construir_catalogo_linaje,
)
from app.services.documentation_preview_service import (
    build_documentation_preview,
)
from app.services.process_documentation_service import (
    ProcessAlreadyDocumentedError,
    document_process,
)
from app.services.process_persistence_mapper import (
    InvalidProcessAnalysisError,
)
from app.services.documented_process_catalog_service import (
    DocumentedProcessNameConflictError,
    list_documented_processes,
    update_documented_process,
)
from app.services.documented_process_preview_service import (
    build_documented_process_preview,
)
from app.services.documented_diagram_service import (
    get_documented_diagram,
)
from app.services.documented_lineage_service import (
    build_documented_lineage_catalog,
    build_documented_variable_route,
)
from app.services.lineage_route_service import (
    construir_ruta_variable,
)
from app.services.repository_registry_service import (
    RepositoryNameConflictError,
    find_repository_by_fingerprint,
    list_repositories,
    rename_repository,
)
from app.services.repository_kettle_update_service import (
    update_repository_from_kettle_upload,
)


app = FastAPI(
    title="Sistema de Documentación ETL",
    version="0.1.0",
)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

RUTA_XML_PRUEBA = (
    RAIZ_PROYECTO
    / "pruebas_locales"
    / "20260611.xml"
)

DIRECTORIO_TEMPORAL = (
    RAIZ_PROYECTO
    / "archivos_temporales"
)

DIRECTORIO_RESULTADOS = (
    RAIZ_PROYECTO
    / "resultados_temporales"
)

@app.get("/health")
def comprobar_estado() -> dict[str, str]:
    return {
        "estado": "correcto",
        "servicio": "backend_etl",
    }


@app.get("/analisis/prueba")
def ejecutar_analisis_prueba() -> dict:
    """
    Endpoint temporal utilizado para comprobar la integración
    entre FastAPI y el motor de análisis.
    """

    try:
        resultado = analizar_proceso_etl(
            ruta_xml=RUTA_XML_PRUEBA,
            carpeta="/MODELOS/TRANSACCIONES",
            nombre_job="job_Macro_Transacciones",
        )

        return {
            "analisis": resultado["analisis"],
            "resumen": resultado["resumen"],
            "seguridad": resultado["seguridad"],
        }

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible ejecutar el análisis de prueba.",
        ) from error


@app.post("/repositorios/cargar")
def cargar_repositorio(
    archivo: Annotated[
        UploadFile,
        File(
            description=(
                "Archivo XML exportado desde un repositorio Pentaho."
            )
        ),
    ],
    session: Annotated[Session, Depends(get_database_session)],
) -> dict:
    """
    Recibe temporalmente un repositorio Pentaho y devuelve
    su inventario básico.
    """

    try:
        carga = guardar_repositorio_temporal(
            archivo_origen=archivo.file,
            nombre_original=archivo.filename,
            directorio_temporal=DIRECTORIO_TEMPORAL,
        )

        inventario = carga["inventario"]
        existing_repository = find_repository_by_fingerprint(
            session,
            carga["huella_sha256"],
        )

        return {
            "id_carga": carga["id_carga"],
            "archivo": carga["nombre_original"],
            "tamano_bytes": carga["tamano_bytes"],
            "huella_sha256": carga["huella_sha256"],
            "repositorio_existente": existing_repository,
            "estado": "cargado_temporalmente",
            "inventario": {
                "jobs": inventario["jobs"],
                "transformaciones": inventario[
                    "transformaciones"
                ],
                "cantidad_carpetas": inventario[
                    "cantidad_carpetas"
                ],
                "carpetas": inventario["carpetas"],
            },
        }

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible procesar el repositorio.",
        ) from error

    finally:
        archivo.file.close()


@app.get("/repositorios")
def consultar_repositorios(
    session: Annotated[Session, Depends(get_database_session)],
) -> dict:
    """Lista las identidades de repositorio disponibles para actualizar."""

    try:
        repositories = list_repositories(session)
        return {
            "total": len(repositories),
            "repositorios": repositories,
        }
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible recuperar los repositorios.",
        ) from error


@app.patch("/repositorios/{repositorio_id}")
def renombrar_repositorio_existente(
    repositorio_id: int,
    solicitud: SolicitudRenombrarRepositorio,
    session: Annotated[Session, Depends(get_database_session)],
) -> dict:
    """Cambia el nombre funcional sin alterar versiones ni procesos."""

    try:
        return rename_repository(
            session=session,
            repository_id=repositorio_id,
            name=solicitud.nombre,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RepositoryNameConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible renombrar el repositorio.",
        ) from error


@app.post("/repositorios/{repositorio_id}/kettle-properties")
async def actualizar_kettle_repositorio_documentado(
    repositorio_id: int,
    session: Annotated[Session, Depends(get_database_session)],
    kettle: UploadFile = File(...),
) -> dict:
    """Resuelve tablas documentadas usando un Kettle temporal."""

    try:
        return await update_repository_from_kettle_upload(
            session=session,
            repository_id=repositorio_id,
            kettle=kettle,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=(
                "No fue posible actualizar las tablas documentadas "
                "con la configuracion Kettle."
            ),
        ) from error
    finally:
        await kettle.close()


@app.get("/repositorios/{id_carga}/jobs")
def consultar_jobs_por_carpeta(
    id_carga: str,
    carpeta: str = Query(
        ...,
        description="Carpeta del repositorio que se desea consultar.",
    ),
) -> dict:
    """
    Devuelve los Jobs disponibles dentro de una carpeta
    del repositorio cargado temporalmente.
    """

    try:
        ruta_temporal = obtener_ruta_repositorio_temporal(
            id_carga=id_carga,
            directorio_temporal=DIRECTORIO_TEMPORAL,
        )

        resultado = obtener_jobs_por_carpeta(
            ruta_xml=ruta_temporal,
            carpeta=carpeta,
        )

        return {
            "id_carga": id_carga,
            "carpeta": resultado["carpeta"],
            "cantidad_jobs": resultado["cantidad_jobs"],
            "jobs": resultado["jobs"],
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible consultar los Jobs.",
        ) from error

@app.post("/repositorios/{id_carga}/analizar")
def analizar_repositorio_temporal(
    id_carga: str,
    solicitud: SolicitudAnalisis,
) -> dict:
    """
    Analiza el Job seleccionado, guarda los metadatos
    sanitizados y elimina el XML temporal.
    """

    try:
        ruta_temporal = obtener_ruta_repositorio_temporal(
            id_carga=id_carga,
            directorio_temporal=DIRECTORIO_TEMPORAL,
        )
        ruta_kettle = obtener_ruta_kettle_temporal(
            id_carga=id_carga,
            directorio_temporal=DIRECTORIO_TEMPORAL,
        )

        resultado = analizar_proceso_etl(
            ruta_xml=ruta_temporal,
            carpeta=solicitud.carpeta,
            nombre_job=solicitud.nombre_job,
            ruta_kettle=ruta_kettle,
            nombre_repositorio=solicitud.nombre_repositorio,
            repositorio_id=solicitud.repositorio_id,
        )

        guardado = guardar_resultado_analisis(
            resultado=resultado,
            directorio_resultados=DIRECTORIO_RESULTADOS,
        )

        # El XML se elimina únicamente después de guardar
        # correctamente el resultado sanitizado.
        ruta_temporal.unlink(missing_ok=True)

        return {
            "id_analisis": guardado["id_analisis"],
            "analisis": resultado["analisis"],
            "resumen": resultado["resumen"],
            "seguridad": resultado["seguridad"],
            "almacenamiento": {
                "resultado_guardado": True,
                "tamano_bytes": guardado["tamano_bytes"],
                "xml_temporal_eliminado": True,
            },
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible analizar y guardar el proceso ETL.",
        ) from error

@app.get("/analisis/{id_analisis}/resumen")
def consultar_resumen_analisis(
    id_analisis: str,
) -> dict:
    """
    Recupera el resumen de un análisis almacenado sin utilizar
    nuevamente el repositorio XML.
    """

    try:
        resultado = obtener_resultado_analisis(
            id_analisis=id_analisis,
            directorio_resultados=DIRECTORIO_RESULTADOS,
        )

        analisis = resultado["analisis"]

        return {
            "id_analisis": id_analisis,
            "analisis": {
                "carpeta": analisis["carpeta"],
                "job_principal": analisis["job_principal"],
                "fecha_analisis": analisis["fecha_analisis"],
                "duracion_segundos": analisis[
                    "duracion_segundos"
                ],
                "estado": analisis["estado"],
            },
            "resumen": resultado["resumen"],
            "seguridad": resultado["seguridad"],
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible recuperar el análisis.",
        ) from error

@app.post(
    "/repositorios/{id_carga}/complementos"
)
async def cargar_complementos_repositorio(
    id_carga: str,
    kettle: UploadFile | None = File(
        default=None,
    ),
) -> dict:
    """
    Guarda kettle.properties asociado al repositorio.
    Los scripts se agregarán posteriormente desde el frontend.
    """

    try:
        obtener_ruta_repositorio_temporal(
            id_carga=id_carga,
            directorio_temporal=DIRECTORIO_TEMPORAL,
        )

        resultado = await guardar_archivos_adicionales(
            id_carga=id_carga,
            directorio_temporal=DIRECTORIO_TEMPORAL,
            kettle=kettle,
            scripts=None,
        )

        return {
            "id_carga": id_carga,
            **resultado,
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=(
                "No fue posible guardar "
                "kettle.properties."
            ),
        ) from error

@app.get(
    "/repositorios/{id_carga}/carpetas"
)
def consultar_carpetas_repositorio(
    id_carga: str,
) -> dict:
    """
    Devuelve las carpetas disponibles en el repositorio.
    """

    try:
        ruta_repositorio = (
            obtener_ruta_repositorio_temporal(
                id_carga=id_carga,
                directorio_temporal=DIRECTORIO_TEMPORAL,
            )
        )

        carpetas = obtener_carpetas_repositorio(
            ruta_xml=ruta_repositorio,
        )

        return {
            "id_carga": id_carga,
            "total_carpetas": len(carpetas),
            "carpetas": carpetas,
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=(
                "No fue posible consultar las carpetas "
                "del repositorio."
            ),
        ) from error


@app.get(
    "/repositorios/{id_carga}/contenido"
)
def consultar_contenido_carpeta(
    id_carga: str,
    carpeta: str = Query(
        description=(
            "Ruta de la carpeta dentro del repositorio."
        ),
    ),
) -> dict:
    """
    Devuelve los Jobs y transformaciones que pertenecen
    directamente a la carpeta seleccionada.
    """

    try:
        ruta_repositorio = (
            obtener_ruta_repositorio_temporal(
                id_carga=id_carga,
                directorio_temporal=DIRECTORIO_TEMPORAL,
            )
        )

        contenido = obtener_contenido_carpeta(
            ruta_xml=ruta_repositorio,
            carpeta=carpeta,
        )

        return {
            "id_carga": id_carga,
            **contenido,
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=(
                "No fue posible consultar el contenido "
                "de la carpeta."
            ),
        ) from error

    
@app.get("/analisis/{id_analisis}/diagrama")
def consultar_diagrama_analisis(
    id_analisis: str,
    proceso_id: str | None = Query(
        default=None,
        description=(
            "Identificador del proceso que se desea visualizar. "
            "Si se omite, se devuelve el Job principal."
        ),
    ),
) -> dict:
    """
    Devuelve el diagrama principal o el de un proceso alcanzable.
    """

    try:
        analisis_guardado = obtener_resultado_analisis(
            id_analisis=id_analisis,
            directorio_resultados=DIRECTORIO_RESULTADOS,
        )

        resultado = analisis_guardado.get("resultado", {})
        diagramas = resultado.get("diagramas", {})
        diagrama_principal_id = resultado.get(
            "diagrama_principal_id"
        )

        if not isinstance(diagramas, dict) or not diagramas:
            raise HTTPException(
                status_code=404,
                detail=(
                    "El análisis no contiene diagramas "
                    "reconstruidos."
                ),
            )

        identificador = proceso_id or diagrama_principal_id

        if not identificador:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No se encontró el identificador "
                    "del diagrama principal."
                ),
            )

        diagrama = diagramas.get(identificador)

        if not isinstance(diagrama, dict):
            raise HTTPException(
                status_code=404,
                detail="No se encontró el diagrama solicitado.",
            )

        return {
            "id_analisis": id_analisis,
            "diagrama_principal_id": diagrama_principal_id,
            "es_principal": identificador
            == diagrama_principal_id,
            "diagrama": diagrama,
        }

    except HTTPException:
        raise

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except (ValueError, TypeError) as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible recuperar el diagrama.",
        ) from error


@app.get("/analisis/{id_analisis}/lineage")
def consultar_catalogo_linaje(
    id_analisis: str,
) -> dict:
    """
    Devuelve el inventario seguro de variables y parametros
    del proceso reconstruido.
    """

    try:
        analisis_guardado = obtener_resultado_analisis(
            id_analisis=id_analisis,
            directorio_resultados=DIRECTORIO_RESULTADOS,
        )

        catalogo = construir_catalogo_linaje(
            analisis_guardado
        )

        return {
            "id_analisis": id_analisis,
            **catalogo,
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except (ValueError, TypeError) as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible recuperar el linaje.",
        ) from error


@app.get("/analisis/{id_analisis}/documentacion/preview")
def consultar_preview_documentacion(
    id_analisis: str,
) -> dict:
    """Devuelve lo que se almacenaria, sin escribir en PostgreSQL."""

    try:
        analisis_guardado = obtener_resultado_analisis(
            id_analisis=id_analisis,
            directorio_resultados=DIRECTORIO_RESULTADOS,
        )

        return {
            "id_analisis": id_analisis,
            **build_documentation_preview(analisis_guardado),
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except (ValueError, TypeError) as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible generar la vista previa.",
        ) from error


@app.post("/repositorios/{id_carga}/analizar-lote")
def analizar_repositorio_por_lote(
    id_carga: str,
    solicitud: SolicitudAnalisisLote,
) -> dict:
    """Reconstruye varios Jobs usando un solo inventario del XML."""

    try:
        ruta_temporal = obtener_ruta_repositorio_temporal(
            id_carga=id_carga,
            directorio_temporal=DIRECTORIO_TEMPORAL,
        )
        ruta_kettle = obtener_ruta_kettle_temporal(
            id_carga=id_carga,
            directorio_temporal=DIRECTORIO_TEMPORAL,
        )

        selections = [
            process.model_dump()
            for process in solicitud.procesos
        ]
        batch_results = analizar_lote_procesos_etl(
            ruta_xml=ruta_temporal,
            procesos=selections,
            nombre_repositorio=solicitud.nombre_repositorio,
            repositorio_id=solicitud.repositorio_id,
            ruta_kettle=ruta_kettle,
        )

        response_items: list[dict] = []

        for item in batch_results:
            if item["estado"] == "fallido":
                response_items.append(item)
                continue

            stored = guardar_resultado_analisis(
                resultado=item["analisis"],
                directorio_resultados=DIRECTORIO_RESULTADOS,
            )
            response_items.append(
                {
                    key: value
                    for key, value in item.items()
                    if key != "analisis"
                }
                | {
                    "id_analisis": stored["id_analisis"],
                    "resumen": item["analisis"]["resumen"],
                }
            )

        completed = sum(
            item["estado"] == "completado"
            for item in response_items
        )
        xml_deleted = completed == len(response_items)

        if xml_deleted:
            ruta_temporal.unlink(missing_ok=True)

        return {
            "id_carga": id_carga,
            "total": len(response_items),
            "completados": completed,
            "fallidos": len(response_items) - completed,
            "procesos": response_items,
            "xml_temporal_eliminado": xml_deleted,
        }

    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible analizar el lote de procesos.",
        ) from error


@app.get("/analisis/{id_analisis}/lineage/variable")
def consultar_ruta_variable_analisis(
    id_analisis: str,
    nombre: str = Query(..., min_length=1),
) -> dict:
    """Reconstruye la ruta de una variable del analisis temporal."""

    try:
        analisis_guardado = obtener_resultado_analisis(
            id_analisis=id_analisis,
            directorio_resultados=DIRECTORIO_RESULTADOS,
        )
        return {
            "id_analisis": id_analisis,
            "ruta": construir_ruta_variable(analisis_guardado, nombre),
        }
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible reconstruir la ruta de la variable.",
        ) from error


@app.post(
    "/analisis/{id_analisis}/documentacion",
    status_code=201,
)
def guardar_documentacion_proceso(
    id_analisis: str,
    solicitud: SolicitudDocumentacion,
    session: Annotated[Session, Depends(get_database_session)],
) -> dict:
    """Guarda una sola vez el proceso reconstruido y sus diagramas."""

    try:
        analisis_guardado = obtener_resultado_analisis(
            id_analisis=id_analisis,
            directorio_resultados=DIRECTORIO_RESULTADOS,
        )
        documentacion = document_process(
            session=session,
            analysis=analisis_guardado,
            process_name=solicitud.nombre_proceso,
            description=solicitud.descripcion,
        )

        return {
            "id_analisis": id_analisis,
            "estado": "documentado",
            **documentacion,
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except ProcessAlreadyDocumentedError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except RepositoryNameConflictError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except (InvalidProcessAnalysisError, ValueError, TypeError) as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible guardar la documentación del proceso.",
        ) from error


@app.post("/analisis/documentacion/lote")
def guardar_documentacion_por_lote(
    solicitud: SolicitudDocumentacionLote,
    session: Annotated[Session, Depends(get_database_session)],
) -> dict:
    """Documenta resultados independientes sin cancelar todo el lote."""

    results: list[dict] = []

    for item in solicitud.procesos:
        try:
            analysis = obtener_resultado_analisis(
                id_analisis=item.id_analisis,
                directorio_resultados=DIRECTORIO_RESULTADOS,
            )
            documentation = document_process(
                session=session,
                analysis=analysis,
                process_name=item.nombre_proceso,
                description=item.descripcion,
            )
            results.append(
                {
                    "id_analisis": item.id_analisis,
                    "nombre_proceso": item.nombre_proceso,
                    "estado": "documentado",
                    **documentation,
                }
            )
        except (
            FileNotFoundError,
            ProcessAlreadyDocumentedError,
            RepositoryNameConflictError,
            InvalidProcessAnalysisError,
            ValueError,
            TypeError,
        ) as error:
            results.append(
                {
                    "id_analisis": item.id_analisis,
                    "nombre_proceso": item.nombre_proceso,
                    "estado": "fallido",
                    "error": str(error),
                }
            )
        except Exception:
            results.append(
                {
                    "id_analisis": item.id_analisis,
                    "nombre_proceso": item.nombre_proceso,
                    "estado": "fallido",
                    "error": "No fue posible documentar este proceso.",
                }
            )

    completed = sum(item["estado"] == "documentado" for item in results)

    return {
        "total": len(results),
        "documentados": completed,
        "fallidos": len(results) - completed,
        "procesos": results,
    }


@app.get("/procesos-documentados")
def consultar_procesos_documentados(
    session: Annotated[Session, Depends(get_database_session)],
) -> dict:
    """Lista los procesos registrados permanentemente en PostgreSQL."""

    try:
        processes = list_documented_processes(session)

        return {
            "total": len(processes),
            "procesos": processes,
        }
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible recuperar los procesos documentados.",
        ) from error


@app.get("/procesos-documentados/{proceso_id}/documentacion")
def consultar_documentacion_guardada(
    proceso_id: int,
    session: Annotated[Session, Depends(get_database_session)],
) -> dict:
    """Reconstruye Documentation usando solo datos de PostgreSQL."""

    try:
        return {
            "id_analisis": f"db-{proceso_id}",
            **build_documented_process_preview(session, proceso_id),
        }
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible recuperar la documentación guardada.",
        ) from error


@app.patch("/procesos-documentados/{proceso_id}")
def actualizar_proceso_documentado(
    proceso_id: int,
    solicitud: SolicitudActualizacionProceso,
    session: Annotated[Session, Depends(get_database_session)],
) -> dict:
    """Modifica el nombre funcional y la descripcion opcional."""

    try:
        return update_documented_process(
            session=session,
            process_id=proceso_id,
            name=solicitud.nombre,
            description=solicitud.descripcion,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DocumentedProcessNameConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible actualizar el proceso documentado.",
        ) from error


@app.get("/procesos-documentados/{proceso_id}/diagrama")
def consultar_diagrama_documentado(
    proceso_id: int,
    session: Annotated[Session, Depends(get_database_session)],
    componente_id: int | None = Query(default=None),
) -> dict:
    """Recupera un diagrama persistido sin volver a cargar el XML."""



    try:
        return get_documented_diagram(
            session=session,
            process_id=proceso_id,
            component_id=componente_id,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible recuperar el diagrama documentado.",
        ) from error


@app.get("/procesos-documentados/{proceso_id}/lineage")
def consultar_linaje_documentado(
    proceso_id: int,
    session: Annotated[Session, Depends(get_database_session)],
) -> dict:
    """Recupera el linaje permanente del proceso documentado."""

    try:
        return build_documented_lineage_catalog(session, proceso_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible recuperar el linaje documentado.",
        ) from error


@app.get("/procesos-documentados/{proceso_id}/lineage/variable")
def consultar_ruta_variable_documentada(
    proceso_id: int,
    session: Annotated[Session, Depends(get_database_session)],
    nombre: str = Query(..., min_length=1),
) -> dict:
    """Reconstruye una ruta variable usando metadatos de PostgreSQL."""

    try:
        return build_documented_variable_route(
            session=session,
            process_id=proceso_id,
            variable_name=nombre,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="No fue posible reconstruir la ruta documentada.",
        ) from error
