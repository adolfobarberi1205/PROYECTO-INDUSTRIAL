from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def _dictionary(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _technical_detail_from_snapshot(
    session: Session,
    process_id: int,
    component_source_key: str,
) -> dict[str, Any]:
    snapshot = session.execute(
        text(
            """
            SELECT snapshot_documentacion
            FROM version_proceso_etl
            WHERE proceso_id = :process_id
            ORDER BY es_actual DESC, numero_version DESC
            LIMIT 1
            """
        ),
        {"process_id": process_id},
    ).scalar_one_or_none()

    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except json.JSONDecodeError:
            snapshot = None

    analysis = _dictionary(snapshot)
    result = _dictionary(analysis.get("resultado"))
    diagrams = _dictionary(result.get("diagramas"))
    stored_diagram = _dictionary(diagrams.get(component_source_key))

    return _dictionary(stored_diagram.get("detalle_tecnico"))


def _technical_detail_from_normalized_tables(
    session: Session,
    process_id: int,
    component_id: int,
    elements: list[dict[str, Any]],
    hops: list[dict[str, Any]],
) -> dict[str, Any]:
    detail = session.execute(
        text(
            """
            SELECT detail.id, detail.total_pasos, detail.total_hops,
                   detail.tiene_sql
            FROM detalle_transformacion AS detail
            JOIN version_proceso_etl AS process_version
              ON process_version.id = detail.version_proceso_id
            WHERE process_version.proceso_id = :process_id
              AND detail.componente_id = :component_id
            ORDER BY process_version.es_actual DESC,
                     process_version.numero_version DESC
            LIMIT 1
            """
        ),
        {"process_id": process_id, "component_id": component_id},
    ).mappings().one_or_none()

    if detail is None:
        return {}

    scripts = session.execute(
        text(
            """
            SELECT nombre_paso, tipo_paso, sql_sanitizado
            FROM script_sql_transformacion
            WHERE detalle_transformacion_id = :detail_id
            ORDER BY orden, id
            """
        ),
        {"detail_id": detail["id"]},
    ).mappings().all()
    tables = session.execute(
        text(
            """
            SELECT nombre_paso, esquema, nombre, nombre_completo, tipo_uso
            FROM tabla_transformacion
            WHERE detalle_transformacion_id = :detail_id
            ORDER BY orden, id
            """
        ),
        {"detail_id": detail["id"]},
    ).mappings().all()
    variables = session.execute(
        text(
            """
            SELECT DISTINCT variable.nombre
            FROM variable_etl AS variable
            JOIN uso_variable AS usage ON usage.variable_id = variable.id
            WHERE variable.proceso_id = :process_id
              AND usage.componente_id = :component_id
            ORDER BY variable.nombre
            """
        ),
        {"process_id": process_id, "component_id": component_id},
    ).scalars().all()
    parameters = session.execute(
        text(
            """
            SELECT DISTINCT parameter.nombre
            FROM parametro_etl AS parameter
            JOIN uso_parametro AS usage ON usage.parametro_id = parameter.id
            WHERE parameter.proceso_id = :process_id
              AND usage.componente_id = :component_id
            ORDER BY parameter.nombre
            """
        ),
        {"process_id": process_id, "component_id": component_id},
    ).scalars().all()

    def serialize_table(row: Any) -> dict[str, str]:
        return {
            "esquema": str(row.get("esquema") or ""),
            "nombre": str(row.get("nombre") or ""),
            "nombre_completo": str(row.get("nombre_completo") or ""),
        }

    def unique_tables(usage_types: set[str]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[str] = set()

        for row in tables:
            if row["tipo_uso"] not in usage_types:
                continue
            key = str(row["nombre_completo"] or "").casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(serialize_table(row))

        return result

    serialized_scripts: list[dict[str, Any]] = []
    for script in scripts:
        step_name = str(script["nombre_paso"] or "")
        script_tables = [
            row
            for row in tables
            if str(row["nombre_paso"] or "") == step_name
        ]
        serialized_scripts.append(
            {
                "paso": step_name,
                "tipo_paso": str(script["tipo_paso"] or ""),
                "sql": str(script["sql_sanitizado"] or ""),
                "tablas_consultadas": [
                    serialize_table(row)
                    for row in script_tables
                    if row["tipo_uso"] in {"CONSULTA", "LOOKUP"}
                ],
                "tablas_destino": [
                    serialize_table(row)
                    for row in script_tables
                    if row["tipo_uso"] == "DESTINO"
                ],
            }
        )

    type_counts: dict[str, int] = {}
    for element in elements:
        step_type = str(element.get("tipo") or "UNKNOWN")
        type_counts[step_type] = type_counts.get(step_type, 0) + 1

    return {
        "total_pasos": int(detail["total_pasos"] or len(elements)),
        "total_hops": int(detail["total_hops"] or len(hops)),
        "tipos_paso": [
            {"tipo": step_type, "cantidad": count}
            for step_type, count in sorted(
                type_counts.items(), key=lambda item: item[0].casefold()
            )
        ],
        "tablas_consultadas": unique_tables({"CONSULTA", "LOOKUP"}),
        "tablas_destino": unique_tables({"DESTINO"}),
        "tiene_sql": bool(detail["tiene_sql"] and serialized_scripts),
        "scripts_sql": serialized_scripts,
        "variables": [str(value) for value in variables],
        "parametros": [str(value) for value in parameters],
        "detalle_historico_limitado": False,
    }


def _technical_detail_from_database(
    session: Session,
    process_id: int,
    component_id: int,
    elements: list[dict[str, Any]],
    hops: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recupera el detalle disponible para versiones documentadas antiguas."""

    destination_rows = session.execute(
        text(
            """
            SELECT DISTINCT
                dwh.nombre,
                dwh.nombre_resuelto,
                dwh.esquema,
                dwh.esquema_resuelto,
                dwh.nombre_completo
            FROM tabla_dwh AS dwh
            JOIN deteccion_tabla_dwh AS detection
              ON detection.tabla_dwh_id = dwh.id
            WHERE dwh.proceso_id = :process_id
              AND detection.componente_id = :component_id
            ORDER BY dwh.nombre_completo
            """
        ),
        {"process_id": process_id, "component_id": component_id},
    ).mappings().all()

    source_rows = session.execute(
        text(
            """
            SELECT DISTINCT source.esquema, source.nombre
            FROM fuente_tabla_dwh AS source
            JOIN tabla_dwh AS dwh ON dwh.id = source.tabla_dwh_id
            JOIN deteccion_tabla_dwh AS detection
              ON detection.tabla_dwh_id = dwh.id
            WHERE dwh.proceso_id = :process_id
              AND detection.componente_id = :component_id
            ORDER BY source.esquema, source.nombre
            """
        ),
        {"process_id": process_id, "component_id": component_id},
    ).mappings().all()

    variable_rows = session.execute(
        text(
            """
            SELECT DISTINCT variable.nombre
            FROM variable_etl AS variable
            JOIN uso_variable AS usage ON usage.variable_id = variable.id
            WHERE variable.proceso_id = :process_id
              AND usage.componente_id = :component_id
            ORDER BY variable.nombre
            """
        ),
        {"process_id": process_id, "component_id": component_id},
    ).scalars().all()

    parameter_rows = session.execute(
        text(
            """
            SELECT DISTINCT parameter.nombre
            FROM parametro_etl AS parameter
            JOIN uso_parametro AS usage ON usage.parametro_id = parameter.id
            WHERE parameter.proceso_id = :process_id
              AND usage.componente_id = :component_id
            ORDER BY parameter.nombre
            """
        ),
        {"process_id": process_id, "component_id": component_id},
    ).scalars().all()

    type_counts: dict[str, int] = {}
    for element in elements:
        step_type = str(element.get("tipo") or "UNKNOWN")
        type_counts[step_type] = type_counts.get(step_type, 0) + 1

    def serialize_table(row: Any, *, destination: bool) -> dict[str, str]:
        if destination:
            schema = row.get("esquema_resuelto") or row.get("esquema") or ""
            name = row.get("nombre_resuelto") or row.get("nombre") or ""
            full_name = row.get("nombre_completo") or ""
        else:
            schema = row.get("esquema") or ""
            name = row.get("nombre") or ""
            full_name = f"{schema}.{name}" if schema else name

        return {
            "esquema": str(schema),
            "nombre": str(name),
            "nombre_completo": str(full_name),
        }

    return {
        "total_pasos": len(elements),
        "total_hops": len(hops),
        "tipos_paso": [
            {"tipo": step_type, "cantidad": count}
            for step_type, count in sorted(
                type_counts.items(), key=lambda item: item[0].casefold()
            )
        ],
        "tablas_consultadas": [
            serialize_table(row, destination=False) for row in source_rows
        ],
        "tablas_destino": [
            serialize_table(row, destination=True) for row in destination_rows
        ],
        "tiene_sql": False,
        "scripts_sql": [],
        "variables": [str(value) for value in variable_rows],
        "parametros": [str(value) for value in parameter_rows],
        "detalle_historico_limitado": True,
    }


def _process_context(session: Session, process_id: int) -> dict[str, Any]:
    process = session.execute(
        text(
            """
            SELECT p.id, p.job_principal_id
            FROM proceso_etl AS p
            WHERE p.id = :process_id
            """
        ),
        {"process_id": process_id},
    ).mappings().one_or_none()

    if process is None:
        raise FileNotFoundError(
            f"No existe el proceso documentado con id '{process_id}'."
        )

    return dict(process)


def get_documented_diagram(
    session: Session,
    process_id: int,
    component_id: int | None = None,
) -> dict[str, Any]:
    """Reconstruye un diagrama navegable usando solo PostgreSQL."""

    process = _process_context(session, process_id)
    requested_id = component_id or int(process["job_principal_id"])

    diagram = session.execute(
        text(
            """
            SELECT d.id, d.componente_id, d.ancho_recomendado,
                   d.alto_recomendado, c.clave_origen,
                   c.nombre, c.tipo, c.ruta
            FROM diagrama AS d
            JOIN componente AS c ON c.id = d.componente_id
            JOIN proceso_componente AS pc
              ON pc.componente_id = c.id
             AND pc.proceso_id = :process_id
            WHERE d.componente_id = :component_id
            """
        ),
        {
            "process_id": process_id,
            "component_id": requested_id,
        },
    ).mappings().one_or_none()

    if diagram is None:
        raise FileNotFoundError(
            "No se encontro el diagrama solicitado dentro del proceso."
        )

    elements = session.execute(
        text(
            """
            SELECT element.clave_origen, element.nombre, element.tipo,
                   element.categoria, element.x_visual, element.y_visual,
                   element.habilitado, element.navegable,
                   element.componente_destino_id, element.estado_destino,
                   target.nombre AS destino_nombre,
                   target.tipo AS destino_tipo,
                   target.ruta AS destino_ruta,
                   target_pc.componente_id IS NOT NULL AS destino_en_proceso
            FROM elemento_diagrama AS element
            LEFT JOIN componente AS target
              ON target.id = element.componente_destino_id
            LEFT JOIN proceso_componente AS target_pc
              ON target_pc.componente_id = target.id
             AND target_pc.proceso_id = :process_id
            WHERE element.diagrama_id = :diagram_id
            ORDER BY element.orden, element.id
            """
        ),
        {
            "process_id": process_id,
            "diagram_id": diagram["id"],
        },
    ).mappings().all()

    hops = session.execute(
        text(
            """
            SELECT nombre_origen, nombre_destino, habilitado,
                   evaluacion, incondicional
            FROM hop_diagrama
            WHERE diagrama_id = :diagram_id
            ORDER BY orden, id
            """
        ),
        {"diagram_id": diagram["id"]},
    ).mappings().all()

    serialized_elements: list[dict[str, Any]] = []
    for element in elements:
        target_id = element["componente_destino_id"]
        target = None
        navigable = bool(
            element["navegable"]
            and target_id is not None
            and element["destino_en_proceso"]
        )

        if target_id is not None or element["estado_destino"]:
            target = {
                "id": str(target_id) if target_id is not None else None,
                "nombre": element["destino_nombre"] or "",
                "tipo": (
                    "TRANSFORMATION"
                    if element["destino_tipo"] == "TRANSFORMACION"
                    else element["destino_tipo"] or ""
                ),
                "ruta": element["destino_ruta"] or "",
                "estado": element["estado_destino"] or "",
                "navegable": navigable,
            }

        serialized_elements.append(
            {
                "id": element["clave_origen"],
                "nombre": element["nombre"],
                "tipo": element["tipo"],
                "categoria": element["categoria"],
                "x": float(element["x_visual"]),
                "y": float(element["y_visual"]),
                "habilitado": bool(element["habilitado"]),
                "target": target,
                "navegable": navigable,
            }
        )

    serialized_hops = [
        {
            "origen": hop["nombre_origen"],
            "destino": hop["nombre_destino"],
            "habilitado": bool(hop["habilitado"]),
            "evaluacion": hop["evaluacion"] or "",
            "incondicional": bool(hop["incondicional"]),
        }
        for hop in hops
    ]

    component_type = (
        "TRANSFORMATION"
        if diagram["tipo"] == "TRANSFORMACION"
        else "JOB"
    )

    technical_detail = None
    if component_type == "TRANSFORMATION":
        technical_detail = _technical_detail_from_normalized_tables(
            session=session,
            process_id=process_id,
            component_id=int(diagram["componente_id"]),
            elements=serialized_elements,
            hops=serialized_hops,
        )

        if not technical_detail:
            technical_detail = _technical_detail_from_snapshot(
                session=session,
                process_id=process_id,
                component_source_key=str(diagram["clave_origen"]),
            )

        if not technical_detail:
            technical_detail = _technical_detail_from_database(
                session=session,
                process_id=process_id,
                component_id=int(diagram["componente_id"]),
                elements=serialized_elements,
                hops=serialized_hops,
            )

    return {
        "id_analisis": f"db-{process_id}",
        "diagrama_principal_id": str(process["job_principal_id"]),
        "es_principal": requested_id == process["job_principal_id"],
        "diagrama": {
            "id": str(diagram["componente_id"]),
            "nombre": diagram["nombre"],
            "tipo": component_type,
            "ruta": diagram["ruta"] or "",
            "elementos": serialized_elements,
            "hops": serialized_hops,
            "detalle_tecnico": technical_detail,
            "total_elementos": len(serialized_elements),
            "total_hops": len(serialized_hops),
            "ancho_recomendado": int(diagram["ancho_recomendado"]),
            "alto_recomendado": int(diagram["alto_recomendado"]),
        },
    }
