from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.lineage_catalog_service import construir_catalogo_linaje


def _dictionary(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return _text(value).casefold()


def _component_lookup(
    session: Session,
    process_id: int,
) -> tuple[dict[str, int], dict[str, int]]:
    rows = session.execute(
        text(
            """
            SELECT c.id, c.clave_origen, c.nombre
            FROM componente AS c
            JOIN proceso_componente AS pc ON pc.componente_id = c.id
            WHERE pc.proceso_id = :proceso_id
            """
        ),
        {"proceso_id": process_id},
    ).mappings()

    by_source_key: dict[str, int] = {}
    ids_by_name: dict[str, list[int]] = defaultdict(list)

    for row in rows:
        component_id = int(row["id"])
        by_source_key[_text(row["clave_origen"])] = component_id
        ids_by_name[_normalized(row["nombre"])].append(component_id)

    by_unique_name = {
        name: ids[0]
        for name, ids in ids_by_name.items()
        if len(ids) == 1
    }
    return by_source_key, by_unique_name


def _resolve_component_id(
    source_key_or_name: Any,
    by_source_key: dict[str, int],
    by_name: dict[str, int],
) -> int | None:
    value = _text(source_key_or_name)

    return by_source_key.get(value) or by_name.get(value.casefold())


def _clear_existing_metadata(session: Session, process_id: int) -> None:
    session.execute(
        text("DELETE FROM transicion_variable WHERE proceso_id = :id"),
        {"id": process_id},
    )
    session.execute(
        text("DELETE FROM tabla_dwh WHERE proceso_id = :id"),
        {"id": process_id},
    )
    session.execute(
        text("DELETE FROM variable_etl WHERE proceso_id = :id"),
        {"id": process_id},
    )
    session.execute(
        text("DELETE FROM parametro_etl WHERE proceso_id = :id"),
        {"id": process_id},
    )


def _save_variables_and_parameters(
    session: Session,
    process_id: int,
    analysis: dict[str, Any],
    by_source_key: dict[str, int],
    by_name: dict[str, int],
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    catalog = construir_catalogo_linaje(analysis)
    variables = _list(catalog.get("variables"))
    parameters = _list(catalog.get("parametros"))
    result = _dictionary(analysis.get("resultado"))

    variable_ids: dict[str, int] = {}
    parameter_ids: dict[str, int] = {}
    counts = {
        "variables": 0,
        "usos_variables": 0,
        "transiciones_variables": 0,
        "parametros": 0,
        "usos_parametros": 0,
        "asignaciones_variable_parametro": 0,
    }

    variable_statement = text(
        """
        INSERT INTO variable_etl (proceso_id, nombre, alcance)
        VALUES (:proceso_id, :nombre, :alcance)
        ON CONFLICT (proceso_id, nombre)
        DO UPDATE SET alcance = EXCLUDED.alcance
        RETURNING id
        """
    )

    def ensure_variable(name: Any, scope: Any = None) -> int | None:
        clean_name = _text(name)
        if not clean_name:
            return None
        key = clean_name.casefold()
        if key not in variable_ids:
            variable_ids[key] = int(
                session.execute(
                    variable_statement,
                    {
                        "proceso_id": process_id,
                        "nombre": clean_name,
                        "alcance": _text(scope) or None,
                    },
                ).scalar_one()
            )
            counts["variables"] += 1
        return variable_ids[key]

    usage_statement = text(
        """
        INSERT INTO uso_variable (
            variable_id, componente_id, componente_nombre,
            tipo_uso, evidencia, orden
        ) VALUES (
            :variable_id, :componente_id, :componente_nombre,
            :tipo_uso, :evidencia, :orden
        )
        """
    )

    for variable in variables:
        if not isinstance(variable, dict):
            continue
        variable_id = ensure_variable(
            variable.get("nombre"),
            variable.get("alcance"),
        )
        if variable_id is None:
            continue

        usage_order = 0
        for usage_type, values in (
            ("PROCESO", variable.get("procesos")),
            ("COMPONENTE", variable.get("componentes")),
            ("EVIDENCIA", variable.get("evidencias")),
        ):
            for value in _list(values):
                clean_value = _text(value)
                if not clean_value:
                    continue
                session.execute(
                    usage_statement,
                    {
                        "variable_id": variable_id,
                        "componente_id": _resolve_component_id(
                            clean_value, by_source_key, by_name
                        ),
                        "componente_nombre": (
                            clean_value if usage_type != "EVIDENCIA" else None
                        ),
                        "tipo_uso": usage_type,
                        "evidencia": (
                            clean_value if usage_type == "EVIDENCIA" else None
                        ),
                        "orden": usage_order,
                    },
                )
                usage_order += 1
                counts["usos_variables"] += 1

    parameter_statement = text(
        """
        INSERT INTO parametro_etl (proceso_id, nombre)
        VALUES (:proceso_id, :nombre)
        ON CONFLICT (proceso_id, nombre)
        DO UPDATE SET nombre = EXCLUDED.nombre
        RETURNING id
        """
    )

    def ensure_parameter(name: Any) -> int | None:
        clean_name = _text(name)
        if not clean_name:
            return None
        key = clean_name.casefold()
        if key not in parameter_ids:
            parameter_ids[key] = int(
                session.execute(
                    parameter_statement,
                    {"proceso_id": process_id, "nombre": clean_name},
                ).scalar_one()
            )
            counts["parametros"] += 1
        return parameter_ids[key]

    parameter_usage_statement = text(
        """
        INSERT INTO uso_parametro (
            parametro_id, componente_id, componente_nombre, tipo_uso, orden
        ) VALUES (
            :parametro_id, :componente_id, :componente_nombre, :tipo_uso, :orden
        )
        """
    )

    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        parameter_id = ensure_parameter(parameter.get("nombre"))
        if parameter_id is None:
            continue
        usage_order = 0
        for usage_type, values in (
            ("DECLARADO", parameter.get("declarado_en")),
            ("REFERENCIADO", parameter.get("referenciado_en")),
            ("LLAMADA", parameter.get("aparece_en_llamadas")),
        ):
            for value in _list(values):
                clean_value = _text(value)
                if not clean_value:
                    continue
                session.execute(
                    parameter_usage_statement,
                    {
                        "parametro_id": parameter_id,
                        "componente_id": _resolve_component_id(
                            clean_value, by_source_key, by_name
                        ),
                        "componente_nombre": clean_value,
                        "tipo_uso": usage_type,
                        "orden": usage_order,
                    },
                )
                usage_order += 1
                counts["usos_parametros"] += 1

    assignment_statement = text(
        """
        INSERT INTO asignacion_variable_parametro (
            proceso_id, variable_id, parametro_id,
            componente_id, componente_nombre
        ) VALUES (
            :proceso_id, :variable_id, :parametro_id,
            :componente_id, :componente_nombre
        )
        ON CONFLICT DO NOTHING
        """
    )

    for variable in variables:
        if not isinstance(variable, dict):
            continue
        variable_id = variable_ids.get(_normalized(variable.get("nombre")))
        if variable_id is None:
            continue
        for parameter_reference in _list(
            variable.get("parametros_alimentados")
        ):
            reference = _text(parameter_reference)
            component_name, separator, parameter_name = reference.rpartition(":")
            if not separator:
                component_name = ""
                parameter_name = reference
            parameter_id = parameter_ids.get(_normalized(parameter_name))
            if parameter_id is None:
                continue
            insertion = session.execute(
                assignment_statement,
                {
                    "proceso_id": process_id,
                    "variable_id": variable_id,
                    "parametro_id": parameter_id,
                    "componente_id": _resolve_component_id(
                        component_name, by_source_key, by_name
                    ),
                    "componente_nombre": component_name,
                },
            )
            counts["asignaciones_variable_parametro"] += max(
                insertion.rowcount or 0, 0
            )

    transition_statement = text(
        """
        INSERT INTO transicion_variable (
            proceso_id, variable_origen_id, variable_destino_id,
            variable_origen_nombre, variable_destino_nombre,
            componente_id, tipo_evento, componente_nombre,
            detalle, estado, alcance, orden
        ) VALUES (
            :proceso_id, :variable_origen_id, :variable_destino_id,
            :variable_origen_nombre, :variable_destino_nombre,
            :componente_id, :tipo_evento, :componente_nombre,
            :detalle, :estado, :alcance, :orden
        )
        """
    )
    raw_variables = _dictionary(result.get("variables"))

    for order, transition in enumerate(_list(raw_variables.get("transiciones"))):
        if not isinstance(transition, dict):
            continue
        source_variable_name = _text(transition.get("variable_origen"))
        target_variable_name = _text(transition.get("variable_destino"))
        source_variable_id = variable_ids.get(source_variable_name.casefold())
        target_variable_id = variable_ids.get(target_variable_name.casefold())
        if not source_variable_name and not target_variable_name:
            continue
        component_key = transition.get("proceso_id") or transition.get(
            "componente"
        )
        session.execute(
            transition_statement,
            {
                "proceso_id": process_id,
                "variable_origen_id": source_variable_id,
                "variable_destino_id": target_variable_id,
                "variable_origen_nombre": source_variable_name or None,
                "variable_destino_nombre": target_variable_name or None,
                "componente_id": _resolve_component_id(
                    component_key, by_source_key, by_name
                ),
                "tipo_evento": _text(transition.get("tipo_evento")) or None,
                "componente_nombre": (
                    _text(transition.get("componente")) or None
                ),
                "detalle": _text(transition.get("detalle")) or None,
                "estado": _text(transition.get("estado")) or None,
                "alcance": _text(transition.get("alcance")) or None,
                "orden": order,
            },
        )
        counts["transiciones_variables"] += 1

    return variable_ids, parameter_ids, counts


def _table_lookup_keys(table: dict[str, Any]) -> set[str]:
    return {
        _normalized(value)
        for value in (
            table.get("nombre_completo"),
            table.get("nombre_resuelto"),
            table.get("nombre"),
        )
        if _text(value)
    }


def _save_field(
    session: Session,
    table_id: int,
    field: dict[str, Any],
    order: int,
    fact: bool,
) -> int | None:
    name = _text(
        field.get("campo_fact")
        if fact
        else field.get("campo_dimension")
    ) or _text(field.get("nombre"))
    if not name:
        return None

    field_id = int(
        session.execute(
            text(
                """
                INSERT INTO campo_tabla_dwh (
                    tabla_dwh_id, nombre, campo_flujo, rol,
                    tipo_origen, detalle, orden
                ) VALUES (
                    :tabla_id, :nombre, :campo_flujo, :rol,
                    :tipo_origen, :detalle, :orden
                )
                ON CONFLICT (tabla_dwh_id, nombre)
                DO UPDATE SET
                    campo_flujo = EXCLUDED.campo_flujo,
                    rol = EXCLUDED.rol,
                    tipo_origen = EXCLUDED.tipo_origen,
                    detalle = EXCLUDED.detalle,
                    orden = EXCLUDED.orden
                RETURNING id
                """
            ),
            {
                "tabla_id": table_id,
                "nombre": name,
                "campo_flujo": _text(field.get("campo_flujo")) or None,
                "rol": _text(field.get("rol")) or None,
                "tipo_origen": _text(field.get("tipo_origen")) or None,
                "detalle": _text(field.get("detalle")) or None,
                "orden": order,
            },
        ).scalar_one()
    )

    origin_statement = text(
        """
        INSERT INTO origen_campo_dwh (campo_id, categoria, nombre, orden)
        VALUES (:campo_id, :categoria, :nombre, :orden)
        """
    )
    origin_order = 0
    for category, key in (
        ("CAMPO", "campos_origen"),
        ("TABLA", "tablas_origen"),
        ("FUENTE", "fuentes_origen"),
    ):
        for value in _list(field.get(key)):
            clean_value = _text(value)
            if not clean_value:
                continue
            session.execute(
                origin_statement,
                {
                    "campo_id": field_id,
                    "categoria": category,
                    "nombre": clean_value,
                    "orden": origin_order,
                },
            )
            origin_order += 1

    for key in _list(field.get("claves_dimension")):
        if isinstance(key, dict):
            value = json.dumps(key, ensure_ascii=False, sort_keys=True)
        else:
            value = _text(key)
        if value:
            session.execute(
                origin_statement,
                {
                    "campo_id": field_id,
                    "categoria": "CLAVE_DIMENSION",
                    "nombre": value,
                    "orden": origin_order,
                },
            )
            origin_order += 1

    for step in _list(field.get("pasos")):
        value = (
            json.dumps(step, ensure_ascii=False, sort_keys=True)
            if isinstance(step, dict)
            else _text(step)
        )
        if value:
            session.execute(
                origin_statement,
                {
                    "campo_id": field_id,
                    "categoria": "PASO",
                    "nombre": value,
                    "orden": origin_order,
                },
            )
            origin_order += 1

    return field_id


def _save_warehouse_metadata(
    session: Session,
    process_id: int,
    analysis: dict[str, Any],
    by_source_key: dict[str, int],
    by_name: dict[str, int],
    variable_ids: dict[str, int],
) -> dict[str, int]:
    result = _dictionary(analysis.get("resultado"))
    warehouse = _dictionary(result.get("tablas_dwh"))
    dimensions = [x for x in _list(warehouse.get("dimensiones")) if isinstance(x, dict)]
    facts = [x for x in _list(warehouse.get("hechos")) if isinstance(x, dict)]
    counts = {
        "tablas_dwh": 0,
        "dimensiones": 0,
        "hechos": 0,
        "detecciones_tablas": 0,
        "campos_dwh": 0,
        "origenes_campos": 0,
        "relaciones_fact_dimension": 0,
        "fuentes_dimensiones": 0,
        "etapas_construccion": 0,
    }
    table_ids_by_key: dict[tuple[str, str], int] = {}

    table_statement = text(
        """
        INSERT INTO tabla_dwh (
            proceso_id, categoria, nombre, nombre_resuelto,
            esquema, esquema_resuelto, nombre_completo
        ) VALUES (
            :proceso_id, :categoria, :nombre, :nombre_resuelto,
            :esquema, :esquema_resuelto, :nombre_completo
        )
        ON CONFLICT (proceso_id, categoria, nombre_completo)
        DO UPDATE SET
            nombre = EXCLUDED.nombre,
            nombre_resuelto = EXCLUDED.nombre_resuelto,
            esquema = EXCLUDED.esquema,
            esquema_resuelto = EXCLUDED.esquema_resuelto
        RETURNING id
        """
    )
    detection_statement = text(
        """
        INSERT INTO deteccion_tabla_dwh (
            tabla_dwh_id, componente_id, transformacion,
            ruta_transformacion, step, tipo_step,
            operacion, criterio, confianza, orden
        ) VALUES (
            :tabla_id, :componente_id, :transformacion,
            :ruta, :step, :tipo_step,
            :operacion, :criterio, :confianza, :orden
        )
        """
    )

    for category, tables in (("DIMENSION", dimensions), ("FACT", facts)):
        for table in tables:
            full_name = _text(table.get("nombre_completo")) or _text(
                table.get("nombre_resuelto") or table.get("nombre")
            )
            if not full_name:
                continue
            table_id = int(
                session.execute(
                    table_statement,
                    {
                        "proceso_id": process_id,
                        "categoria": category,
                        "nombre": _text(table.get("nombre")) or full_name,
                        "nombre_resuelto": (
                            _text(table.get("nombre_resuelto")) or None
                        ),
                        "esquema": _text(table.get("esquema")) or None,
                        "esquema_resuelto": (
                            _text(table.get("esquema_resuelto")) or None
                        ),
                        "nombre_completo": full_name,
                    },
                ).scalar_one()
            )
            for lookup_key in _table_lookup_keys(table):
                table_ids_by_key[(category, lookup_key)] = table_id
            counts["tablas_dwh"] += 1
            counts["dimensiones" if category == "DIMENSION" else "hechos"] += 1

            for order, detection in enumerate(_list(table.get("detecciones"))):
                if not isinstance(detection, dict):
                    continue
                component_key = detection.get("proceso_id") or detection.get(
                    "transformacion"
                )
                session.execute(
                    detection_statement,
                    {
                        "tabla_id": table_id,
                        "componente_id": _resolve_component_id(
                            component_key, by_source_key, by_name
                        ),
                        "transformacion": (
                            _text(detection.get("transformacion")) or None
                        ),
                        "ruta": (
                            _text(detection.get("ruta_transformacion")) or None
                        ),
                        "step": _text(detection.get("step")) or None,
                        "tipo_step": _text(detection.get("tipo_step")) or None,
                        "operacion": _text(detection.get("operacion")) or None,
                        "criterio": _text(detection.get("criterio")) or None,
                        "confianza": _text(detection.get("confianza")) or None,
                        "orden": order,
                    },
                )
                counts["detecciones_tablas"] += 1

    def find_table_id(category: str, value: dict[str, Any]) -> int | None:
        for lookup_key in _table_lookup_keys(value):
            table_id = table_ids_by_key.get((category, lookup_key))
            if table_id is not None:
                return table_id
        return None

    fact_relations = _dictionary(result.get("relaciones_fact_dim"))
    fact_dimension_statement = text(
        """
        INSERT INTO relacion_fact_dimension (fact_id, dimension_id)
        VALUES (:fact_id, :dimension_id)
        ON CONFLICT DO NOTHING
        """
    )

    for fact in _list(fact_relations.get("hechos")):
        if not isinstance(fact, dict):
            continue
        fact_id = find_table_id("FACT", fact)
        if fact_id is None:
            continue
        for field_order, field in enumerate(_list(fact.get("linaje_campos"))):
            if not isinstance(field, dict):
                continue
            field_id = _save_field(session, fact_id, field, field_order, True)
            if field_id is not None:
                counts["campos_dwh"] += 1
                counts["origenes_campos"] += sum(
                    len(_list(field.get(key)))
                    for key in (
                        "campos_origen", "tablas_origen", "fuentes_origen",
                        "claves_dimension", "pasos",
                    )
                )
        for dimension in _list(fact.get("dimensiones")):
            if not isinstance(dimension, dict):
                continue
            dimension_id = find_table_id("DIMENSION", dimension)
            if dimension_id is None:
                continue
            insertion = session.execute(
                fact_dimension_statement,
                {"fact_id": fact_id, "dimension_id": dimension_id},
            )
            counts["relaciones_fact_dimension"] += max(
                insertion.rowcount or 0, 0
            )

    dimension_construction = _dictionary(
        result.get("construccion_dimensiones")
    )
    source_statement = text(
        """
        INSERT INTO fuente_tabla_dwh (
            tabla_dwh_id, tipo_fuente, nombre, esquema, orden
        ) VALUES (:tabla_id, :tipo, :nombre, :esquema, :orden)
        """
    )
    stage_statement = text(
        """
        INSERT INTO etapa_construccion_tabla (
            tabla_dwh_id, componente_id, nombre, tipo, funcion, orden
        ) VALUES (
            :tabla_id, :componente_id, :nombre, :tipo, :funcion, :orden
        )
        """
    )
    table_variable_statement = text(
        """
        INSERT INTO tabla_dwh_variable (tabla_dwh_id, variable_id, tipo_uso)
        VALUES (:tabla_id, :variable_id, 'CONSTRUCCION')
        ON CONFLICT DO NOTHING
        """
    )

    for dimension in _list(dimension_construction.get("dimensiones")):
        if not isinstance(dimension, dict):
            continue
        table_id = find_table_id("DIMENSION", dimension)
        if table_id is None:
            continue
        source_order = 0
        source_seen: set[tuple[str, str, str]] = set()
        for source in _list(dimension.get("tablas_fuente")):
            if not isinstance(source, dict):
                continue
            source_key = (
                "TABLA",
                _text(source.get("nombre")),
                _text(source.get("esquema")),
            )
            if not source_key[1] or source_key in source_seen:
                continue
            source_seen.add(source_key)
            session.execute(
                source_statement,
                {
                    "tabla_id": table_id,
                    "tipo": source_key[0],
                    "nombre": source_key[1],
                    "esquema": source_key[2] or None,
                    "orden": source_order,
                },
            )
            source_order += 1
            counts["fuentes_dimensiones"] += 1
        for source in _list(dimension.get("fuentes_origen")):
            clean_source = _text(source)
            source_key = ("FUENTE", clean_source, "")
            if not clean_source or source_key in source_seen:
                continue
            source_seen.add(source_key)
            session.execute(
                source_statement,
                {
                    "tabla_id": table_id,
                    "tipo": "FUENTE",
                    "nombre": clean_source,
                    "esquema": None,
                    "orden": source_order,
                },
            )
            source_order += 1
            counts["fuentes_dimensiones"] += 1

        stage_order = 0
        for stage in (
            _list(dimension.get("transformaciones"))
            + _list(dimension.get("transformaciones_auxiliares"))
            + _list(dimension.get("pasos"))
        ):
            if isinstance(stage, dict):
                name = _text(
                    stage.get("nombre") or stage.get("transformacion")
                )
                stage_type = _text(stage.get("tipo")) or None
                function = _text(
                    stage.get("funcion") or stage.get("paso")
                ) or None
            else:
                name = _text(stage)
                stage_type = None
                function = None
            if not name:
                continue
            session.execute(
                stage_statement,
                {
                    "tabla_id": table_id,
                    "componente_id": _resolve_component_id(
                        name, by_source_key, by_name
                    ),
                    "nombre": name,
                    "tipo": stage_type,
                    "funcion": function,
                    "orden": stage_order,
                },
            )
            stage_order += 1
            counts["etapas_construccion"] += 1

        for variable_name in _list(dimension.get("variables")):
            variable_id = variable_ids.get(_normalized(variable_name))
            if variable_id is not None:
                session.execute(
                    table_variable_statement,
                    {"tabla_id": table_id, "variable_id": variable_id},
                )

        for field_order, field in enumerate(_list(dimension.get("linaje_campos"))):
            if not isinstance(field, dict):
                continue
            field_id = _save_field(session, table_id, field, field_order, False)
            if field_id is not None:
                counts["campos_dwh"] += 1
                counts["origenes_campos"] += sum(
                    len(_list(field.get(key)))
                    for key in (
                        "campos_origen", "tablas_origen", "fuentes_origen",
                        "claves_dimension", "pasos",
                    )
                )

    return counts


def save_analysis_metadata(
    *,
    session: Session,
    process_id: int,
    analysis: dict[str, Any],
) -> dict[str, int]:
    """Persiste DWH, campos, variables y parametros sin valores sensibles."""

    by_source_key, by_name = _component_lookup(session, process_id)
    _clear_existing_metadata(session, process_id)
    variable_ids, _parameter_ids, lineage_counts = (
        _save_variables_and_parameters(
            session,
            process_id,
            analysis,
            by_source_key,
            by_name,
        )
    )
    warehouse_counts = _save_warehouse_metadata(
        session,
        process_id,
        analysis,
        by_source_key,
        by_name,
        variable_ids,
    )

    return {**warehouse_counts, **lineage_counts}
