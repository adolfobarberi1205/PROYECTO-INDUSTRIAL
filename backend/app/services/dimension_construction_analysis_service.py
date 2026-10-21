from __future__ import annotations

from collections import deque
import re
from typing import Any
import xml.etree.ElementTree as ET

from app.services.fact_field_lineage_service import trace_dimension_fields


VARIABLE_PATTERN = re.compile(r"\$\{([^}]+)\}")
IDENTIFIER = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\$\{[^}]+\}|[A-Za-z_][\w$#]*)'
QUALIFIED_IDENTIFIER = rf"{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER}){{0,2}}"
SOURCE_TABLE_PATTERN = re.compile(
    rf"\b(?:FROM|JOIN)\s+({QUALIFIED_IDENTIFIER})",
    re.IGNORECASE,
)
FALSE_VALUES = {"n", "no", "false", "0", "disabled", "inactivo"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _direct_text(element: ET.Element, name: str) -> str:
    child = element.find(f"./{name}")
    return _text(child.text if child is not None else "")


def _clean_identifier(value: str) -> str:
    cleaned = value.strip()

    if len(cleaned) >= 2:
        pairs = {'"': '"', "`": "`", "[": "]"}
        expected = pairs.get(cleaned[0])

        if expected and cleaned[-1] == expected:
            cleaned = cleaned[1:-1]

    return cleaned.strip()


def _split_table_name(value: str) -> tuple[str, str]:
    parts = [
        _clean_identifier(part)
        for part in re.split(r"\s*\.\s*", value)
        if part.strip()
    ]

    if not parts:
        return "", ""

    return ".".join(parts[:-1]), parts[-1]


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


def _source_tables_from_sql(sql: str) -> list[tuple[str, str]]:
    without_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    without_comments = re.sub(r"--[^\r\n]*", " ", without_comments)
    sources: list[tuple[str, str]] = []

    for match in SOURCE_TABLE_PATTERN.finditer(without_comments):
        schema, table = _split_table_name(match.group(1))

        if table and (schema, table) not in sources:
            sources.append((schema, table))

    return sources


def _step_purpose(step_type: str, is_target: bool) -> str:
    if is_target:
        return "ESCRIBE_DIMENSION"

    normalized = step_type.casefold()

    if normalized == "tableinput":
        return "LEE_TABLA"
    if normalized == "getvariable":
        return "LEE_VARIABLE"
    if normalized == "setvariable":
        return "ASIGNA_VARIABLE"
    if normalized in {"simplemapping", "mapping"}:
        return "EJECUTA_TRANSFORMACION_AUXILIAR"
    if normalized == "dimensionlookup":
        return "CONSULTA_DIMENSION"

    return "TRANSFORMA_DATOS"


def _analyze_dimension_detection(
    transformation: dict[str, Any],
    target_step: str,
) -> dict[str, Any]:
    xml_element = transformation.get("xml_element")

    if not isinstance(xml_element, ET.Element):
        return {
            "tablas_fuente": [],
            "fuentes_origen": [],
            "variables": [],
            "transformaciones_auxiliares": [],
            "pasos": [],
            "campos_dimension": [],
        }

    steps = {
        _direct_text(step, "name"): step
        for step in xml_element.findall("./step")
        if _direct_text(step, "name")
    }
    target = steps.get(target_step)

    if target is None:
        return {
            "tablas_fuente": [],
            "fuentes_origen": [],
            "variables": [],
            "transformaciones_auxiliares": [],
            "pasos": [],
            "campos_dimension": [],
        }

    distances = _upstream_distances(
        _reverse_graph(xml_element),
        target_step,
    )
    source_tables: dict[tuple[str, str], dict[str, Any]] = {}
    original_sources: dict[tuple[str, str], dict[str, Any]] = {}
    variables: set[str] = set()
    auxiliary_transformations: dict[tuple[str, str], dict[str, str]] = {}
    flow_steps: list[dict[str, Any]] = []

    for step_name, distance in distances.items():
        step = steps.get(step_name)

        if step is None:
            continue

        step_type = _direct_text(step, "type")
        flow_steps.append(
            {
                "nombre": step_name,
                "tipo": step_type,
                "funcion": _step_purpose(
                    step_type,
                    step_name == target_step,
                ),
                "distancia_salida": distance,
            }
        )

        serialized_step = ET.tostring(step, encoding="unicode")
        variables.update(
            name.strip()
            for name in VARIABLE_PATTERN.findall(serialized_step)
            if name.strip()
        )

        normalized_type = step_type.casefold()

        if normalized_type == "tableinput":
            for schema, table in _source_tables_from_sql(
                _direct_text(step, "sql")
            ):
                source_tables[(schema.casefold(), table.casefold())] = {
                    "nombre": table,
                    "esquema": schema,
                    "paso": step_name,
                    "tipo_acceso": "CONSULTA_SQL",
                }

        elif normalized_type == "excelinput":
            source_name = f"Excel: {step_name}"
            original_sources[("archivo_excel", step_name.casefold())] = {
                "nombre": source_name,
                "tipo": "ARCHIVO_EXCEL",
                "paso": step_name,
            }

        elif normalized_type in {
            "databaselookup",
            "dimensionlookup",
        } and step_name != target_step:
            table = _direct_text(step, "table")
            schema = _direct_text(step, "schema")

            if table:
                source_tables[(schema.casefold(), table.casefold())] = {
                    "nombre": table,
                    "esquema": schema,
                    "paso": step_name,
                    "tipo_acceso": "CONSULTA_LOOKUP",
                }

        if normalized_type in {"simplemapping", "mapping"}:
            name = _direct_text(step, "trans_name")
            path = _direct_text(step, "directory_path")

            if name:
                auxiliary_transformations[(path.casefold(), name.casefold())] = {
                    "nombre": name,
                    "ruta": path,
                    "paso": step_name,
                }

    dimension_fields: list[dict[str, str]] = []

    if _direct_text(target, "type").casefold() == "dimensionlookup":
        for field in target.findall("./fields/*"):
            stream_field = _direct_text(field, "name")
            dimension_field = _direct_text(field, "lookup")
            update_type = _direct_text(field, "update")

            if stream_field or dimension_field:
                dimension_fields.append(
                    {
                        "campo_entrada": stream_field,
                        "campo_dimension": dimension_field,
                        "operacion": update_type,
                    }
                )

    flow_steps.sort(
        key=lambda item: (-item["distancia_salida"], item["nombre"].casefold())
    )

    return {
        "tablas_fuente": sorted(
            source_tables.values(),
            key=lambda item: item["nombre"].casefold(),
        ),
        "fuentes_origen": sorted(
            original_sources.values(),
            key=lambda item: item["nombre"].casefold(),
        ),
        "variables": sorted(variables, key=str.casefold),
        "transformaciones_auxiliares": sorted(
            auxiliary_transformations.values(),
            key=lambda item: item["nombre"].casefold(),
        ),
        "pasos": flow_steps,
        "campos_dimension": dimension_fields,
    }


def analyze_dimension_construction(
    *,
    inventory: dict[str, Any],
    result: dict[str, Any],
    warehouse_tables: dict[str, Any],
) -> dict[str, Any]:
    """Explica las fuentes y operaciones usadas para crear cada DIM."""

    transformation_index = _reachable_transformation_index(inventory, result)
    dimensions: list[dict[str, Any]] = []

    for dimension in warehouse_tables.get("dimensiones", []):
        if not isinstance(dimension, dict):
            continue

        sources: dict[tuple[str, str], dict[str, Any]] = {}
        original_sources: dict[tuple[str, str], dict[str, Any]] = {}
        variables: set[str] = set()
        auxiliaries: dict[tuple[str, str], dict[str, str]] = {}
        steps: dict[tuple[str, str], dict[str, Any]] = {}
        fields: dict[tuple[str, str], dict[str, str]] = {}
        field_lineage: dict[str, dict[str, Any]] = {}
        transformations: set[str] = set()

        for detection in dimension.get("detecciones", []):
            if not isinstance(detection, dict):
                continue

            process_id = _text(detection.get("proceso_id"))
            target_step = _text(detection.get("step"))
            transformation = transformation_index.get(process_id)

            if not transformation or not target_step:
                continue

            transformation_name = _text(transformation.get("nombre"))
            transformations.add(transformation_name)
            detail = _analyze_dimension_detection(transformation, target_step)

            for source in detail["tablas_fuente"]:
                key = (
                    _text(source.get("esquema")).casefold(),
                    _text(source.get("nombre")).casefold(),
                )
                sources[key] = source

            for source in detail["fuentes_origen"]:
                key = (
                    _text(source.get("tipo")).casefold(),
                    _text(source.get("nombre")).casefold(),
                )
                original_sources[key] = source

            variables.update(detail["variables"])

            for auxiliary in detail["transformaciones_auxiliares"]:
                key = (
                    _text(auxiliary.get("ruta")).casefold(),
                    _text(auxiliary.get("nombre")).casefold(),
                )
                auxiliaries[key] = auxiliary

            for step in detail["pasos"]:
                steps[(transformation_name.casefold(), step["nombre"].casefold())] = {
                    **step,
                    "transformacion": transformation_name,
                }

            for field in detail["campos_dimension"]:
                key = (
                    _text(field.get("campo_entrada")).casefold(),
                    _text(field.get("campo_dimension")).casefold(),
                )
                fields[key] = field

            for lineage in trace_dimension_fields(
                transformation,
                target_step,
            ):
                field_lineage.setdefault(
                    _text(lineage.get("campo_dimension")).casefold(),
                    lineage,
                )

        dimensions.append(
            {
                "nombre": dimension.get("nombre", ""),
                "nombre_resuelto": dimension.get("nombre_resuelto", ""),
                "nombre_completo": dimension.get("nombre_completo", ""),
                "transformaciones": sorted(filter(None, transformations)),
                "tablas_fuente": sorted(
                    sources.values(),
                    key=lambda item: item["nombre"].casefold(),
                ),
                "fuentes_origen": sorted(
                    original_sources.values(),
                    key=lambda item: item["nombre"].casefold(),
                ),
                "variables": sorted(variables, key=str.casefold),
                "transformaciones_auxiliares": sorted(
                    auxiliaries.values(),
                    key=lambda item: item["nombre"].casefold(),
                ),
                "pasos": list(steps.values()),
                "campos_dimension": list(fields.values()),
                "linaje_campos": list(field_lineage.values()),
            }
        )

    dimensions.sort(
        key=lambda item: _text(
            item.get("nombre_resuelto") or item.get("nombre")
        ).casefold()
    )

    return {
        "disponible": True,
        "total_dimensiones": len(dimensions),
        "dimensiones": dimensions,
    }
