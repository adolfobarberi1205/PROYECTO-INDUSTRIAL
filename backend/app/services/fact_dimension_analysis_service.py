from __future__ import annotations

from collections import deque
import re
from typing import Any
import xml.etree.ElementTree as ET

from app.services.fact_field_lineage_service import trace_fact_fields


FALSE_VALUES = {"n", "no", "false", "0", "disabled", "inactivo"}
INSERT_FIELDS_PATTERN = re.compile(
    r"\bINSERT\s+INTO\s+[^\s(]+\s*\(([^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _direct_text(element: ET.Element, name: str) -> str:
    child = element.find(f"./{name}")
    return _text(child.text if child is not None else "")


def _reachable_transformation_index(
    inventory: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    nodes = result.get("nodos", {})
    reachable_ids = (
        set(nodes)
        if isinstance(nodes, dict)
        else {
            _text(node.get("id"))
            for node in nodes
            if isinstance(node, dict)
        }
    )

    return {
        process_id: transformation
        for process_id, transformation in inventory.get(
            "transformaciones", {}
        ).items()
        if process_id in reachable_ids
        and isinstance(transformation, dict)
    }


def _reverse_graph(
    transformation_xml: ET.Element,
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}

    for hop in transformation_xml.findall("./order/hop"):
        enabled = _direct_text(hop, "enabled").casefold()

        if enabled in FALSE_VALUES:
            continue

        origin = _direct_text(hop, "from")
        destination = _direct_text(hop, "to")

        if origin and destination:
            graph.setdefault(destination, set()).add(origin)

    return graph


def _upstream_distances(
    reverse_graph: dict[str, set[str]],
    target_step: str,
) -> dict[str, int]:
    distances = {target_step: 0}
    pending: deque[str] = deque([target_step])

    while pending:
        current = pending.popleft()
        next_distance = distances[current] + 1

        for origin in reverse_graph.get(current, set()):
            previous = distances.get(origin)

            if previous is None or next_distance < previous:
                distances[origin] = next_distance
                pending.append(origin)

    return distances


def _dimensions_for_fact_step(
    transformation: dict[str, Any],
    fact_step: str,
) -> list[dict[str, Any]]:
    xml_element = transformation.get("xml_element")

    if not isinstance(xml_element, ET.Element):
        return []

    steps = {
        _direct_text(step, "name"): step
        for step in xml_element.findall("./step")
        if _direct_text(step, "name")
    }
    distances = _upstream_distances(
        _reverse_graph(xml_element),
        fact_step,
    )
    dimensions: dict[tuple[str, str], dict[str, Any]] = {}

    for step_name, distance in distances.items():
        if step_name == fact_step:
            continue

        step = steps.get(step_name)

        if step is None:
            continue

        step_type = _direct_text(step, "type")

        if step_type.casefold() != "dimensionlookup":
            continue

        table = _direct_text(step, "table")
        schema = _direct_text(step, "schema")

        if not table:
            continue

        key = (schema.casefold(), table.casefold())
        update_enabled = _direct_text(step, "update").casefold() in {
            "y",
            "yes",
            "true",
            "1",
        }
        evidence = {
            "nombre": table,
            "esquema": schema,
            "step": step_name,
            "tipo_step": step_type,
            "transformacion": transformation.get("nombre", ""),
            "proceso_id": transformation.get("id", ""),
            "distancia_hops": distance,
            "modo": (
                "ACTUALIZA_Y_CONSULTA"
                if update_enabled
                else "CONSULTA_CLAVE_DIMENSION"
            ),
            "confianza": "ALTA",
        }

        existing = dimensions.get(key)

        if existing is None or distance < existing["distancia_hops"]:
            dimensions[key] = evidence

    return sorted(
        dimensions.values(),
        key=lambda dimension: (
            dimension["distancia_hops"],
            dimension["nombre"].casefold(),
        ),
    )


def _fact_fields_for_step(
    transformation: dict[str, Any],
    fact_step: str,
) -> list[dict[str, str]]:
    """Extrae las columnas declaradas en la salida de una FACT."""

    xml_element = transformation.get("xml_element")

    if not isinstance(xml_element, ET.Element):
        return []

    step = next(
        (
            candidate
            for candidate in xml_element.findall("./step")
            if _direct_text(candidate, "name") == fact_step
        ),
        None,
    )

    if step is None:
        return []

    step_type = _direct_text(step, "type").casefold()
    fields: dict[str, dict[str, str]] = {}

    if step_type in {"tableoutput", "orabulkloader"}:
        for field in step.findall("./fields/field"):
            destination = (
                _direct_text(field, "column_name")
                or _direct_text(field, "name")
                or _direct_text(field, "field_name")
            )
            source = (
                _direct_text(field, "stream_name")
                or _direct_text(field, "field_name")
                or _direct_text(field, "name")
            )

            if not destination:
                continue

            fields.setdefault(
                destination.casefold(),
                {
                    "nombre": destination,
                    "campo_origen": source,
                },
            )

    elif step_type == "execsql":
        sql = _direct_text(step, "sql")
        match = INSERT_FIELDS_PATTERN.search(sql)

        if match:
            for raw_field in match.group(1).split(","):
                field_name = raw_field.strip().strip('"`[]')

                if field_name:
                    fields.setdefault(
                        field_name.casefold(),
                        {
                            "nombre": field_name,
                            "campo_origen": "",
                        },
                    )

    return list(fields.values())


def analyze_fact_dimension_participation(
    *,
    inventory: dict[str, Any],
    result: dict[str, Any],
    warehouse_tables: dict[str, Any],
) -> dict[str, Any]:
    """Relaciona cada salida FACT con sus dimensiones upstream."""

    transformation_index = _reachable_transformation_index(
        inventory,
        result,
    )
    fact_records: list[dict[str, Any]] = []

    for fact in warehouse_tables.get("hechos", []):
        if not isinstance(fact, dict):
            continue

        dimensions: dict[tuple[str, str], dict[str, Any]] = {}
        fact_fields: dict[str, dict[str, str]] = {}
        field_lineage: dict[str, dict[str, Any]] = {}
        transformations: set[str] = set()
        output_steps: set[str] = set()

        for detection in fact.get("detecciones", []):
            if not isinstance(detection, dict):
                continue

            process_id = _text(detection.get("proceso_id"))
            fact_step = _text(detection.get("step"))
            transformation = transformation_index.get(process_id)

            if not transformation or not fact_step:
                continue

            transformations.add(_text(transformation.get("nombre")))
            output_steps.add(fact_step)

            for dimension in _dimensions_for_fact_step(
                transformation,
                fact_step,
            ):
                key = (
                    _text(dimension.get("esquema")).casefold(),
                    _text(dimension.get("nombre")).casefold(),
                )
                existing = dimensions.get(key)

                if (
                    existing is None
                    or dimension["distancia_hops"]
                    < existing["distancia_hops"]
                ):
                    dimensions[key] = dimension

            for field in _fact_fields_for_step(
                transformation,
                fact_step,
            ):
                fact_fields.setdefault(
                    field["nombre"].casefold(),
                    field,
                )

            for lineage in trace_fact_fields(
                transformation,
                fact_step,
            ):
                field_lineage.setdefault(
                    _text(lineage.get("campo_fact")).casefold(),
                    lineage,
                )

        dimension_list = sorted(
            dimensions.values(),
            key=lambda item: item["nombre"].casefold(),
        )
        fact_records.append(
            {
                "nombre": fact.get("nombre", ""),
                "nombre_resuelto": fact.get("nombre_resuelto", ""),
                "esquema": fact.get("esquema", ""),
                "esquema_resuelto": fact.get("esquema_resuelto", ""),
                "nombre_completo": fact.get("nombre_completo", ""),
                "transformaciones": sorted(filter(None, transformations)),
                "steps_salida": sorted(output_steps),
                "total_dimensiones": len(dimension_list),
                "dimensiones": dimension_list,
                "total_campos": len(fact_fields),
                "campos": list(fact_fields.values()),
                "linaje_campos": list(field_lineage.values()),
            }
        )

    fact_records.sort(
        key=lambda fact: _text(
            fact.get("nombre_resuelto") or fact.get("nombre")
        ).casefold()
    )

    return {
        "disponible": True,
        "total_hechos": len(fact_records),
        "total_relaciones": sum(
            fact["total_dimensiones"] for fact in fact_records
        ),
        "hechos_sin_dimensiones": sum(
            1 for fact in fact_records if not fact["dimensiones"]
        ),
        "hechos": fact_records,
        "criterio": (
            "DimensionLookup conectado por hops habilitados a la salida FACT."
        ),
    }
