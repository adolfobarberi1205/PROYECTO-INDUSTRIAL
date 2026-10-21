from __future__ import annotations

import re
from typing import Any
import xml.etree.ElementTree as ET


VARIABLE_PATTERN = re.compile(r"\$\{([^}]+)\}")
IDENTIFIER = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\$\{[^}]+\}|[A-Za-z_][\w$#]*)'
QUALIFIED_IDENTIFIER = rf"{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER}){{0,2}}"

SQL_TARGET_PATTERNS = (
    (
        "CREA_TABLA_SQL",
        re.compile(
            rf"\bCREATE\s+(?:GLOBAL\s+TEMPORARY\s+|TEMPORARY\s+)?"
            rf"TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?({QUALIFIED_IDENTIFIER})",
            re.IGNORECASE,
        ),
    ),
    (
        "CARGA_SQL",
        re.compile(
            rf"\bINSERT\s+INTO\s+({QUALIFIED_IDENTIFIER})",
            re.IGNORECASE,
        ),
    ),
    (
        "CARGA_SQL",
        re.compile(
            rf"\bMERGE\s+INTO\s+({QUALIFIED_IDENTIFIER})",
            re.IGNORECASE,
        ),
    ),
)


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


def _property_index(
    properties: dict[str, dict[str, Any]],
) -> dict[str, str]:
    return {
        _text(name).casefold(): _text(data.get("valor"))
        for name, data in properties.items()
        if isinstance(data, dict)
    }


def _resolve_variables(value: str, properties: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        variable_name = match.group(1).strip().casefold()
        return properties.get(variable_name, match.group(0))

    return VARIABLE_PATTERN.sub(replace, value)


def _classify_table(table_name: str) -> str | None:
    normalized = _clean_identifier(table_name).casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")

    if re.match(r"^(?:dim|dimension)(?:_|$)", normalized):
        return "DIMENSION"

    if re.match(r"^(?:fact|fct|hecho)(?:_|$)", normalized):
        return "FACT"

    return None


def _split_qualified_name(value: str) -> tuple[str, str]:
    parts = [
        _clean_identifier(part)
        for part in re.split(r"\s*\.\s*", value.strip())
        if part.strip()
    ]

    if not parts:
        return "", ""

    return ".".join(parts[:-1]), parts[-1]


def _sql_targets(sql: str) -> list[dict[str, str]]:
    without_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    without_comments = re.sub(r"--[^\r\n]*", " ", without_comments)
    targets: list[dict[str, str]] = []

    for operation, pattern in SQL_TARGET_PATTERNS:
        for match in pattern.finditer(without_comments):
            schema, table = _split_qualified_name(match.group(1))
            targets.append(
                {
                    "schema": schema,
                    "table": table,
                    "operation": operation,
                }
            )

    return targets


def _reachable_transformations(
    inventory: dict[str, Any],
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    nodes = result.get("nodos", {})

    if isinstance(nodes, dict):
        reachable_ids = set(nodes)
    else:
        reachable_ids = {
            _text(node.get("id"))
            for node in nodes
            if isinstance(node, dict)
        }

    transformations = inventory.get("transformaciones", {})

    return [
        transformation
        for process_id, transformation in transformations.items()
        if process_id in reachable_ids
        and isinstance(transformation, dict)
    ]


def _add_detection(
    grouped_tables: dict[tuple[str, str, str], dict[str, Any]],
    *,
    category: str,
    schema: str,
    table: str,
    operation: str,
    criterion: str,
    transformation: dict[str, Any],
    step_name: str,
    step_type: str,
    properties: dict[str, str],
) -> None:
    resolved_schema = _resolve_variables(schema, properties)
    resolved_table = _resolve_variables(table, properties)
    key = (
        category,
        (resolved_schema or schema).casefold(),
        (resolved_table or table).casefold(),
    )
    full_name = (
        f"{resolved_schema}.{resolved_table}"
        if resolved_schema
        else resolved_table
    )

    record = grouped_tables.setdefault(
        key,
        {
            "categoria": category,
            "nombre": table,
            "nombre_resuelto": resolved_table,
            "esquema": schema,
            "esquema_resuelto": resolved_schema,
            "nombre_completo": full_name,
            "operaciones": [],
            "detecciones": [],
        },
    )

    if operation not in record["operaciones"]:
        record["operaciones"].append(operation)

    evidence = {
        "proceso_id": transformation.get("id", ""),
        "transformacion": transformation.get("nombre", ""),
        "ruta_transformacion": transformation.get("ruta", ""),
        "step": step_name,
        "tipo_step": step_type,
        "operacion": operation,
        "criterio": criterion,
        "confianza": "ALTA",
    }

    if evidence not in record["detecciones"]:
        record["detecciones"].append(evidence)


def analyze_warehouse_tables(
    *,
    inventory: dict[str, Any],
    result: dict[str, Any],
    kettle_properties: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Detecta tablas DIM y FACT escritas por el subgrafo reconstruido."""

    properties = _property_index(kettle_properties or {})
    grouped_tables: dict[tuple[str, str, str], dict[str, Any]] = {}
    transformations = _reachable_transformations(inventory, result)

    for transformation in transformations:
        xml_element = transformation.get("xml_element")

        if not isinstance(xml_element, ET.Element):
            continue

        for step in xml_element.findall("./step"):
            step_name = _direct_text(step, "name")
            step_type = _direct_text(step, "type")
            normalized_type = step_type.casefold()

            if normalized_type == "dimensionlookup":
                if _direct_text(step, "update").casefold() not in {
                    "y",
                    "yes",
                    "true",
                    "1",
                }:
                    continue

                schema = _direct_text(step, "schema")
                table = _direct_text(step, "table")

                if table:
                    _add_detection(
                        grouped_tables,
                        category="DIMENSION",
                        schema=schema,
                        table=table,
                        operation="ACTUALIZA_DIMENSION",
                        criterion="DimensionLookup con update=Y",
                        transformation=transformation,
                        step_name=step_name,
                        step_type=step_type,
                        properties=properties,
                    )

                continue

            if normalized_type in {"tableoutput", "orabulkloader"}:
                schema = _direct_text(step, "schema")
                table = _direct_text(step, "table")
                category = _classify_table(table)

                if not category:
                    continue

                operation = (
                    "CARGA_MASIVA"
                    if normalized_type == "orabulkloader"
                    else "CARGA_TABLA"
                )

                _add_detection(
                    grouped_tables,
                    category=category,
                    schema=schema,
                    table=table,
                    operation=operation,
                    criterion=f"Destino explicito de {step_type}",
                    transformation=transformation,
                    step_name=step_name,
                    step_type=step_type,
                    properties=properties,
                )

                continue

            if normalized_type == "execsql":
                for target in _sql_targets(_direct_text(step, "sql")):
                    category = _classify_table(target["table"])

                    if not category:
                        continue

                    _add_detection(
                        grouped_tables,
                        category=category,
                        schema=target["schema"],
                        table=target["table"],
                        operation=target["operation"],
                        criterion="Destino explicito encontrado en ExecSQL",
                        transformation=transformation,
                        step_name=step_name,
                        step_type=step_type,
                        properties=properties,
                    )

    tables = sorted(
        grouped_tables.values(),
        key=lambda item: (
            item["categoria"] != "DIMENSION",
            item["nombre_completo"].casefold(),
        ),
    )
    dimensions = [
        table for table in tables if table["categoria"] == "DIMENSION"
    ]
    facts = [table for table in tables if table["categoria"] == "FACT"]

    return {
        "disponible": True,
        "total": len(tables),
        "total_dimensiones": len(dimensions),
        "total_hechos": len(facts),
        "dimensiones": dimensions,
        "hechos": facts,
        "tablas": tables,
        "criterios": {
            "dimension_lookup": "Solo update=Y; update=N es una consulta.",
            "salidas": "TableOutput y OraBulkLoader con nombre DIM o FACT.",
            "sql": "CREATE TABLE, INSERT INTO o MERGE INTO con destino DIM o FACT.",
        },
    }
