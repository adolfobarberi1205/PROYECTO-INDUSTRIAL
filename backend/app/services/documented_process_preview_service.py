from __future__ import annotations

import json
from collections import Counter
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def _load_field_lineage(
    session: Session,
    table_id: int,
    fact: bool,
) -> list[dict[str, Any]]:
    fields = session.execute(
        text(
            """
            SELECT id, nombre, campo_flujo, rol, tipo_origen, detalle
            FROM campo_tabla_dwh
            WHERE tabla_dwh_id = :table_id
            ORDER BY orden, id
            """
        ),
        {"table_id": table_id},
    ).mappings()
    result: list[dict[str, Any]] = []

    for field in fields:
        origins = session.execute(
            text(
                """
                SELECT categoria, nombre
                FROM origen_campo_dwh
                WHERE campo_id = :field_id
                ORDER BY orden, id
                """
            ),
            {"field_id": field["id"]},
        ).mappings()
        grouped: dict[str, list[Any]] = {
            "CAMPO": [],
            "TABLA": [],
            "FUENTE": [],
            "CLAVE_DIMENSION": [],
            "PASO": [],
        }

        for origin in origins:
            value: Any = origin["nombre"]
            if origin["categoria"] in {"CLAVE_DIMENSION", "PASO"}:
                try:
                    value = json.loads(value)
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            grouped[origin["categoria"]].append(value)

        item = {
            "campo_flujo": field["campo_flujo"] or "",
            "tipo_origen": field["tipo_origen"] or "NO_DETERMINADO",
            "detalle": field["detalle"] or "",
            "campos_origen": grouped["CAMPO"],
            "tablas_origen": grouped["TABLA"],
            "fuentes_origen": grouped["FUENTE"],
            "claves_dimension": grouped["CLAVE_DIMENSION"],
            "pasos": grouped["PASO"],
        }
        if fact:
            item["campo_fact"] = field["nombre"]
        else:
            item["campo_dimension"] = field["nombre"]
            item["rol"] = field["rol"] or ""
        result.append(item)

    return result


def _load_warehouse_tables(
    session: Session,
    process_id: int,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    rows = session.execute(
        text(
            """
            SELECT id, categoria, nombre, nombre_resuelto,
                   esquema, esquema_resuelto, nombre_completo
            FROM tabla_dwh
            WHERE proceso_id = :process_id
            ORDER BY categoria, nombre_resuelto, nombre
            """
        ),
        {"process_id": process_id},
    ).mappings()
    tables: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}

    for row in rows:
        detections = [
            dict(detection)
            for detection in session.execute(
                text(
                    """
                    SELECT COALESCE(c.clave_origen, '') AS proceso_id,
                           transformacion,
                           ruta_transformacion,
                           step,
                           tipo_step,
                           operacion,
                           criterio,
                           confianza
                    FROM deteccion_tabla_dwh AS detection
                    LEFT JOIN componente AS c ON c.id = detection.componente_id
                    WHERE detection.tabla_dwh_id = :table_id
                    ORDER BY detection.orden, detection.id
                    """
                ),
                {"table_id": row["id"]},
            ).mappings()
        ]
        table = {
            "id": int(row["id"]),
            "categoria": row["categoria"],
            "nombre": row["nombre"],
            "nombre_resuelto": row["nombre_resuelto"] or row["nombre"],
            "esquema": row["esquema"] or "",
            "esquema_resuelto": row["esquema_resuelto"] or "",
            "nombre_completo": row["nombre_completo"],
            "operaciones": sorted(
                {
                    str(item["operacion"])
                    for item in detections
                    if item.get("operacion")
                }
            ),
            "detecciones": detections,
        }
        tables.append(table)
        by_id[table["id"]] = table

    return tables, by_id


def build_documented_process_preview(
    session: Session,
    process_id: int,
) -> dict[str, Any]:
    process = session.execute(
        text(
            """
            SELECT p.id, p.nombre, p.descripcion, r.fecha_carga,
                   root.id AS root_id,
                   root.clave_origen AS root_source_key,
                   root.nombre AS root_name,
                   root.tipo AS root_type,
                   root.ruta AS root_path
            FROM proceso_etl AS p
            JOIN repositorio AS r ON r.id = p.repositorio_id
            JOIN componente AS root ON root.id = p.job_principal_id
            WHERE p.id = :process_id
            """
        ),
        {"process_id": process_id},
    ).mappings().one_or_none()

    if process is None:
        raise FileNotFoundError(
            f"No existe el proceso documentado con id '{process_id}'."
        )

    process_rows = session.execute(
        text(
            """
            SELECT c.id, c.clave_origen, c.nombre, c.tipo, c.ruta,
                   COUNT(DISTINCT element.id) AS elementos,
                   COUNT(DISTINCT hop.id) AS hops,
                   COUNT(DISTINCT diagram.id) > 0 AS tiene_diagrama
            FROM proceso_componente AS pc
            JOIN componente AS c ON c.id = pc.componente_id
            LEFT JOIN diagrama AS diagram ON diagram.componente_id = c.id
            LEFT JOIN elemento_diagrama AS element
              ON element.diagrama_id = diagram.id
            LEFT JOIN hop_diagrama AS hop ON hop.diagrama_id = diagram.id
            WHERE pc.proceso_id = :process_id
            GROUP BY c.id, c.clave_origen, c.nombre, c.tipo, c.ruta
            ORDER BY (c.id = :root_id) DESC, c.tipo, c.nombre
            """
        ),
        {"process_id": process_id, "root_id": process["root_id"]},
    ).mappings().all()
    documented_processes = [
        {
            "id": row["clave_origen"],
            "nombre": row["nombre"],
            "tipo": (
                "TRANSFORMATION"
                if row["tipo"] == "TRANSFORMACION"
                else "JOB"
            ),
            "ruta": row["ruta"] or "",
            "es_principal": row["id"] == process["root_id"],
            "elementos": int(row["elementos"] or 0),
            "hops": int(row["hops"] or 0),
            "tiene_diagrama": bool(row["tiene_diagrama"]),
        }
        for row in process_rows
    ]

    relation_count = int(
        session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM relacion_componente AS relation
                WHERE relation.componente_origen_id IN (
                    SELECT componente_id FROM proceso_componente
                    WHERE proceso_id = :process_id
                )
                  AND relation.componente_destino_id IN (
                    SELECT componente_id FROM proceso_componente
                    WHERE proceso_id = :process_id
                )
                """
            ),
            {"process_id": process_id},
        ).scalar_one()
    )
    total_elements = sum(item["elementos"] for item in documented_processes)
    total_hops = sum(item["hops"] for item in documented_processes)
    total_jobs = sum(item["tipo"] == "JOB" for item in documented_processes)
    total_transformations = sum(
        item["tipo"] == "TRANSFORMATION" for item in documented_processes
    )

    warehouse_tables, warehouse_by_id = _load_warehouse_tables(
        session, process_id
    )
    dimensions = [
        table for table in warehouse_tables if table["categoria"] == "DIMENSION"
    ]
    facts = [
        table for table in warehouse_tables if table["categoria"] == "FACT"
    ]
    fact_relations: list[dict[str, Any]] = []

    for fact in facts:
        dimension_rows = session.execute(
            text(
                """
                SELECT dimension_id
                FROM relacion_fact_dimension
                WHERE fact_id = :fact_id
                ORDER BY id
                """
            ),
            {"fact_id": fact["id"]},
        ).scalars()
        related_dimensions = [
            {
                "nombre": warehouse_by_id[dimension_id]["nombre"],
                "nombre_resuelto": warehouse_by_id[dimension_id][
                    "nombre_resuelto"
                ],
            }
            for dimension_id in dimension_rows
            if dimension_id in warehouse_by_id
        ]
        field_lineage = _load_field_lineage(
            session, fact["id"], fact=True
        )
        fact_relations.append(
            {
                "nombre": fact["nombre"],
                "nombre_resuelto": fact["nombre_resuelto"],
                "nombre_completo": fact["nombre_completo"],
                "total_dimensiones": len(related_dimensions),
                "dimensiones": related_dimensions,
                "total_campos": len(field_lineage),
                "campos": [
                    {
                        "nombre": field["campo_fact"],
                        "campo_origen": field["campo_flujo"],
                    }
                    for field in field_lineage
                ],
                "linaje_campos": field_lineage,
            }
        )

    dimension_construction: list[dict[str, Any]] = []

    for dimension in dimensions:
        source_rows = session.execute(
            text(
                """
                SELECT tipo_fuente, nombre, esquema
                FROM fuente_tabla_dwh
                WHERE tabla_dwh_id = :table_id
                ORDER BY orden, id
                """
            ),
            {"table_id": dimension["id"]},
        ).mappings().all()
        stage_rows = session.execute(
            text(
                """
                SELECT stage.nombre, stage.tipo, stage.funcion,
                       COALESCE(component.ruta, '') AS ruta
                FROM etapa_construccion_tabla AS stage
                LEFT JOIN componente AS component
                  ON component.id = stage.componente_id
                WHERE stage.tabla_dwh_id = :table_id
                ORDER BY stage.orden, stage.id
                """
            ),
            {"table_id": dimension["id"]},
        ).mappings().all()
        variables = session.execute(
            text(
                """
                SELECT variable.nombre
                FROM tabla_dwh_variable AS link
                JOIN variable_etl AS variable ON variable.id = link.variable_id
                WHERE link.tabla_dwh_id = :table_id
                ORDER BY variable.nombre
                """
            ),
            {"table_id": dimension["id"]},
        ).scalars().all()
        dimension_construction.append(
            {
                "nombre": dimension["nombre"],
                "nombre_resuelto": dimension["nombre_resuelto"],
                "nombre_completo": dimension["nombre_completo"],
                "tablas_fuente": [
                    {
                        "nombre": row["nombre"],
                        "esquema": row["esquema"] or "",
                    }
                    for row in source_rows
                    if row["tipo_fuente"] == "TABLA"
                ],
                "variables": list(variables),
                "transformaciones_auxiliares": [
                    {"nombre": row["nombre"], "ruta": row["ruta"]}
                    for row in stage_rows
                    if row["ruta"]
                ],
                "pasos": [
                    {
                        "nombre": row["nombre"],
                        "tipo": row["tipo"] or "",
                        "funcion": row["funcion"] or "",
                    }
                    for row in stage_rows
                ],
                "linaje_campos": _load_field_lineage(
                    session, dimension["id"], fact=False
                ),
            }
        )

    variables = [
        dict(row)
        for row in session.execute(
            text(
                """
                SELECT variable.nombre, variable.alcance,
                       COUNT(usage.id) AS usado_en
                FROM variable_etl AS variable
                LEFT JOIN uso_variable AS usage
                  ON usage.variable_id = variable.id
                 AND usage.tipo_uso = 'PROCESO'
                WHERE variable.proceso_id = :process_id
                GROUP BY variable.id, variable.nombre, variable.alcance
                ORDER BY variable.nombre
                """
            ),
            {"process_id": process_id},
        ).mappings()
    ]
    parameters = [
        dict(row)
        for row in session.execute(
            text(
                """
                SELECT parameter.nombre,
                       COUNT(usage.id) FILTER (
                           WHERE usage.tipo_uso = 'DECLARADO'
                       ) AS declaraciones,
                       COUNT(usage.id) FILTER (
                           WHERE usage.tipo_uso = 'REFERENCIADO'
                       ) AS referencias,
                       COUNT(usage.id) FILTER (
                           WHERE usage.tipo_uso = 'LLAMADA'
                       ) AS llamadas
                FROM parametro_etl AS parameter
                LEFT JOIN uso_parametro AS usage
                  ON usage.parametro_id = parameter.id
                WHERE parameter.proceso_id = :process_id
                GROUP BY parameter.id, parameter.nombre
                ORDER BY parameter.nombre
                """
            ),
            {"process_id": process_id},
        ).mappings()
    ]
    relation_statuses = Counter({"RESUELTA": relation_count})

    return {
        "modo": "documented",
        "guardado": True,
        "analisis": {
            "job_principal": process["root_name"],
            "carpeta": process["root_path"] or "",
            "fecha_analisis": (
                process["fecha_carga"].isoformat()
                if process["fecha_carga"] is not None
                else None
            ),
            "estado": "documentado",
        },
        "proceso_principal": {
            "id": process["root_source_key"],
            "nombre": process["nombre"],
            "tipo": "JOB",
            "ruta": process["root_path"] or "",
            "descripcion": process["descripcion"] or "",
        },
        "resumen": {
            "procesos": len(documented_processes),
            "jobs": total_jobs,
            "transformaciones": total_transformations,
            "relaciones": relation_count,
            "diagramas": sum(
                bool(item["tiene_diagrama"]) for item in documented_processes
            ),
            "elementos": total_elements,
            "hops": total_hops,
        },
        "procesos": documented_processes,
        "relaciones_por_estado": dict(relation_statuses),
        "tablas_dwh": {
            "disponible": bool(warehouse_tables),
            "total": len(warehouse_tables),
            "total_dimensiones": len(dimensions),
            "total_hechos": len(facts),
            "dimensiones": dimensions,
            "hechos": facts,
        },
        "relaciones_fact_dim": {
            "disponible": bool(fact_relations),
            "total_hechos": len(fact_relations),
            "total_relaciones": sum(
                item["total_dimensiones"] for item in fact_relations
            ),
            "hechos_sin_dimensiones": sum(
                item["total_dimensiones"] == 0 for item in fact_relations
            ),
            "hechos": fact_relations,
        },
        "construccion_dimensiones": {
            "disponible": bool(dimension_construction),
            "total_dimensiones": len(dimension_construction),
            "dimensiones": dimension_construction,
        },
        "linaje_persistente": {
            "variables": variables,
            "parametros": parameters,
        },
        "almacenamiento": {
            "incluye": [
                "Procesos", "Relaciones", "Diagramas",
                "Elementos y posiciones", "Hops",
                "Tablas DIM y FACT", "Campos y fuentes de datos",
                "Relaciones FACT-DIM",
                "Variables, usos y transiciones",
                "Parametros, usos y asignaciones",
            ],
            "excluye_por_ahora": [
                "XML original", "Valores sensibles",
                "Credenciales y conexiones",
            ],
        },
    }
