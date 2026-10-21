from __future__ import annotations

import re
from typing import Any
import xml.etree.ElementTree as ET


FALSE_VALUES = {"n", "no", "false", "0", "disabled", "inactivo"}
IDENTIFIER = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\$\{[^}]+\}|[A-Za-z_][\w$#]*)'
QUALIFIED_IDENTIFIER = rf"{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER}){{0,2}}"
SOURCE_TABLE_PATTERN = re.compile(
    rf"\b(?:FROM|JOIN)\s+({QUALIFIED_IDENTIFIER})",
    re.IGNORECASE,
)
ALIAS_PATTERN = re.compile(
    r'\s+AS\s+("[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$#]*)\s*$',
    re.IGNORECASE,
)
FIELD_REFERENCE_PATTERN = re.compile(
    r"\b(?:([A-Za-z_][\w$#]*)\.)?([A-Za-z_][\w$#]*)\b"
)
SQL_WORDS = {
    "as",
    "distinct",
    "null",
    "case",
    "when",
    "then",
    "else",
    "end",
    "and",
    "or",
    "not",
    "in",
    "is",
    "like",
    "sum",
    "count",
    "min",
    "max",
    "avg",
    "nvl",
    "decode",
    "substr",
    "lpad",
    "rpad",
    "trim",
    "upper",
    "lower",
    "to_char",
    "to_date",
}


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


def _without_sql_comments(sql: str) -> str:
    value = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\r\n]*", " ", value)


def _source_tables(sql: str) -> list[str]:
    tables: list[str] = []

    for match in SOURCE_TABLE_PATTERN.finditer(_without_sql_comments(sql)):
        raw_table = match.group(1)
        parts = [
            _clean_identifier(part)
            for part in re.split(r"\s*\.\s*", raw_table)
            if part.strip()
        ]
        table = parts[-1] if parts else ""

        if table and table.casefold() not in {
            item.casefold() for item in tables
        }:
            tables.append(table)

    return tables


def _find_top_level_from(sql: str, select_end: int) -> int:
    depth = 0
    quote = ""
    index = select_end

    while index < len(sql):
        character = sql[index]

        if quote:
            if character == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = ""
            index += 1
            continue

        if character in {"'", '"'}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and sql[index : index + 4].casefold() == "from":
            before = sql[index - 1] if index else " "
            after = sql[index + 4] if index + 4 < len(sql) else " "

            if not before.isalnum() and not after.isalnum():
                return index

        index += 1

    return -1


def _split_top_level(value: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    quote = ""
    index = 0

    while index < len(value):
        character = value[index]

        if quote:
            if character == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    index += 2
                    continue
                quote = ""
        elif character in {"'", '"'}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif character == "," and depth == 0:
            items.append(value[start:index].strip())
            start = index + 1

        index += 1

    final_item = value[start:].strip()

    if final_item:
        items.append(final_item)

    return items


def _sql_projection(sql: str) -> dict[str, dict[str, Any]]:
    cleaned = _without_sql_comments(sql)
    select_match = re.search(r"\bSELECT\b", cleaned, re.IGNORECASE)

    if not select_match:
        return {}

    from_index = _find_top_level_from(cleaned, select_match.end())

    if from_index < 0:
        return {}

    projection = cleaned[select_match.end() : from_index].strip()
    projection = re.sub(r"^DISTINCT\b", "", projection, flags=re.IGNORECASE)
    fields: dict[str, dict[str, Any]] = {}

    for item in _split_top_level(projection):
        alias_match = ALIAS_PATTERN.search(item)

        if alias_match:
            alias = _clean_identifier(alias_match.group(1))
            expression = item[: alias_match.start()].strip()
        else:
            expression = item.strip()
            simple_field = re.search(
                r'(?:[A-Za-z_][\w$#]*\.)?([A-Za-z_][\w$#]*)\s*$',
                expression,
            )
            alias = simple_field.group(1) if simple_field else ""

        if not alias:
            continue

        references: list[str] = []

        for table_alias, column in FIELD_REFERENCE_PATTERN.findall(expression):
            if column.casefold() in SQL_WORDS:
                continue

            reference = f"{table_alias}.{column}" if table_alias else column

            if reference.casefold() not in {
                item.casefold() for item in references
            }:
                references.append(reference)

        fields[alias.casefold()] = {
            "campo": alias,
            "expresion": " ".join(expression.split()),
            "referencias": references,
        }

    return fields


def _predecessor_index(xml_element: ET.Element) -> dict[str, list[str]]:
    predecessors: dict[str, list[str]] = {}

    for hop in xml_element.findall("./order/hop"):
        if _direct_text(hop, "enabled").casefold() in FALSE_VALUES:
            continue

        origin = _direct_text(hop, "from")
        destination = _direct_text(hop, "to")

        if origin and destination:
            predecessors.setdefault(destination, []).append(origin)

    return predecessors


def _empty_origin(field_name: str) -> dict[str, Any]:
    return {
        "tipo_origen": "NO_DETERMINADO",
        "detalle": "No fue posible determinar el origen estaticamente.",
        "campos_origen": [field_name] if field_name else [],
        "tablas_origen": [],
        "fuentes_origen": [],
        "claves_dimension": [],
        "pasos": [],
    }


def _merge_origins(
    origins: list[dict[str, Any]],
    *,
    origin_type: str | None = None,
    detail: str | None = None,
    step: dict[str, str] | None = None,
) -> dict[str, Any]:
    determined_origins = [
        origin
        for origin in origins
        if origin.get("tipo_origen") != "NO_DETERMINADO"
    ]

    if determined_origins:
        origins = determined_origins

    if not origins:
        merged = _empty_origin("")
    else:
        merged = {
            "tipo_origen": origin_type or origins[0]["tipo_origen"],
            "detalle": detail or origins[0]["detalle"],
            "campos_origen": [],
            "tablas_origen": [],
            "fuentes_origen": [],
            "claves_dimension": [],
            "pasos": [],
        }

        for origin in origins:
            for key in (
                "campos_origen",
                "tablas_origen",
                "fuentes_origen",
                "claves_dimension",
            ):
                for item in origin.get(key, []):
                    if item not in merged[key]:
                        merged[key].append(item)

            for item in origin.get("pasos", []):
                if item not in merged["pasos"]:
                    merged["pasos"].append(item)

    if origin_type:
        merged["tipo_origen"] = origin_type
    if detail:
        merged["detalle"] = detail
    if step and step not in merged["pasos"]:
        merged["pasos"].append(step)

    return merged


def _script_assignments(script: str) -> dict[str, str]:
    return {
        name.casefold(): expression.strip()
        for name, expression in re.findall(
            r"(?:\bvar\s+)?\b([A-Za-z_][\w$#]*)\s*=\s*([^;]+);",
            script,
            flags=re.IGNORECASE,
        )
    }


def _script_dependencies(
    field_name: str,
    assignments: dict[str, str],
    visited: set[str] | None = None,
) -> list[str]:
    visited = set(visited or set())
    normalized = field_name.casefold()

    if normalized in visited:
        return []

    visited.add(normalized)
    expression = assignments.get(normalized, "")

    if not expression:
        return [field_name]

    dependencies: list[str] = []

    for token in re.findall(r"\b[A-Za-z_][\w$#]*\b", expression):
        token_normalized = token.casefold()

        if token_normalized in {
            "var",
            "substr",
            "switch",
            "case",
            "break",
            "true",
            "false",
            "null",
        }:
            continue

        nested = (
            _script_dependencies(token, assignments, visited)
            if token_normalized in assignments
            else [token]
        )

        for dependency in nested:
            if dependency.casefold() not in {
                item.casefold() for item in dependencies
            }:
                dependencies.append(dependency)

    return dependencies


def _trace_field(
    *,
    step_name: str,
    field_name: str,
    steps: dict[str, ET.Element],
    predecessors: dict[str, list[str]],
    visited: set[tuple[str, str]],
) -> dict[str, Any]:
    visit_key = (step_name.casefold(), field_name.casefold())

    if visit_key in visited:
        return _empty_origin(field_name)

    visited = {*visited, visit_key}
    step = steps.get(step_name)

    if step is None:
        return _empty_origin(field_name)

    step_type = _direct_text(step, "type")
    normalized_type = step_type.casefold()
    step_record = {"nombre": step_name, "tipo": step_type}

    if normalized_type == "tableinput":
        sql = _direct_text(step, "sql")
        projection = _sql_projection(sql).get(field_name.casefold())
        expression = projection.get("expresion", field_name) if projection else field_name
        references = projection.get("referencias", [field_name]) if projection else [field_name]

        return {
            "tipo_origen": "CONSULTA_SQL",
            "detalle": expression,
            "campos_origen": references,
            "tablas_origen": _source_tables(sql),
            "fuentes_origen": [],
            "pasos": [step_record],
        }

    if normalized_type == "getvariable":
        for field in step.findall("./fields/field"):
            if _direct_text(field, "name").casefold() == field_name.casefold():
                variable = _direct_text(field, "variable")
                return {
                    "tipo_origen": "VARIABLE",
                    "detalle": variable,
                    "campos_origen": [variable],
                    "tablas_origen": [],
                    "fuentes_origen": [],
                    "pasos": [step_record],
                }

    if normalized_type == "datagrid":
        declared_fields = {
            _direct_text(field, "name").casefold()
            for field in step.findall("./fields/field")
        }

        if field_name.casefold() in declared_fields:
            return {
                "tipo_origen": "DATOS_INTERNOS",
                "detalle": f"Campo definido dentro del paso {step_name}",
                "campos_origen": [field_name],
                "tablas_origen": [],
                "fuentes_origen": [f"Datos internos: {step_name}"],
                "pasos": [step_record],
            }

    if normalized_type == "excelinput":
        declared_fields = {
            _direct_text(field, "name").casefold()
            for field in step.findall("./fields/field")
        }
        sheet_field = _direct_text(step, "sheetfield")

        if (
            field_name.casefold() in declared_fields
            or field_name.casefold() == sheet_field.casefold()
        ):
            file_mask = _direct_text(step.find("./file") or step, "filemask")
            source_name = f"Excel: {file_mask}" if file_mask else "Archivo Excel"
            return {
                "tipo_origen": "ARCHIVO_EXCEL",
                "detalle": f"Campo leido por {step_name}",
                "campos_origen": [field_name],
                "tablas_origen": [],
                "fuentes_origen": [source_name],
                "pasos": [step_record],
            }

    if normalized_type == "systeminfo":
        for system_field in step.findall("./fields/field"):
            if _direct_text(system_field, "name").casefold() == field_name.casefold():
                information_type = _direct_text(system_field, "type")
                return {
                    "tipo_origen": "INFORMACION_SISTEMA",
                    "detalle": information_type or "Informacion del sistema",
                    "campos_origen": [],
                    "tablas_origen": [],
                    "fuentes_origen": ["Sistema"],
                    "pasos": [step_record],
                }

    if normalized_type == "scriptvaluemod":
        declared_fields = {
            (
                _direct_text(field, "rename")
                or _direct_text(field, "name")
            ).casefold(): _direct_text(field, "name")
            for field in step.findall("./fields/field")
        }

        if field_name.casefold() in declared_fields:
            scripts = "\n".join(
                _direct_text(script, "jsScript_script")
                for script in step.findall("./jsScripts/jsScript")
            )
            assignments = _script_assignments(scripts)
            output_name = declared_fields[field_name.casefold()] or field_name
            expression = assignments.get(output_name.casefold(), output_name)
            dependencies = _script_dependencies(output_name, assignments)
            origins = [
                _trace_predecessors(
                    current_step=step_name,
                    field_name=dependency,
                    steps=steps,
                    predecessors=predecessors,
                    visited=visited,
                )
                for dependency in dependencies
            ]
            return _merge_origins(
                origins,
                origin_type="SCRIPT",
                detail=f"{output_name} = {' '.join(expression.split())}",
                step=step_record,
            )

    if normalized_type == "dimensionlookup":
        table = _direct_text(step, "table")
        dimension_keys = [
            {
                "campo_flujo": _direct_text(key_field, "name"),
                "campo_dimension": (
                    _direct_text(key_field, "lookup")
                    or _direct_text(key_field, "name")
                ),
            }
            for key_field in step.findall("./fields/key")
            if _direct_text(key_field, "name")
            or _direct_text(key_field, "lookup")
        ]

        for return_field in step.findall("./fields/return"):
            output_name = (
                _direct_text(return_field, "rename")
                or _direct_text(return_field, "name")
            )

            if output_name.casefold() == field_name.casefold():
                return {
                    "tipo_origen": "CLAVE_DIMENSION",
                    "detalle": f"Clave obtenida de {table}",
                    "campos_origen": [_direct_text(return_field, "name")],
                    "tablas_origen": [table] if table else [],
                    "fuentes_origen": [],
                    "claves_dimension": dimension_keys,
                    "pasos": [step_record],
                }

        for lookup_field in step.findall("./fields/field"):
            output_name = _direct_text(lookup_field, "name")

            if output_name.casefold() == field_name.casefold():
                lookup_name = _direct_text(lookup_field, "lookup")
                return {
                    "tipo_origen": "ATRIBUTO_DIMENSION",
                    "detalle": f"Campo consultado en {table}",
                    "campos_origen": [lookup_name or output_name],
                    "tablas_origen": [table] if table else [],
                    "fuentes_origen": [],
                    "claves_dimension": dimension_keys,
                    "pasos": [step_record],
                }

    input_field = field_name
    transformation_detail = ""

    if normalized_type == "calculator":
        for calculation in step.findall("./calculation"):
            if _direct_text(calculation, "field_name").casefold() != field_name.casefold():
                continue

            dependencies = [
                _direct_text(calculation, name)
                for name in ("field_a", "field_b", "field_c")
                if _direct_text(calculation, name)
            ]
            calculation_type = _direct_text(calculation, "calc_type")
            origins = [
                _trace_predecessors(
                    current_step=step_name,
                    field_name=dependency,
                    steps=steps,
                    predecessors=predecessors,
                    visited=visited,
                )
                for dependency in dependencies
            ]
            return _merge_origins(
                origins,
                origin_type="CALCULO",
                detail=f"{calculation_type}: {', '.join(dependencies)}",
                step=step_record,
            )

    elif normalized_type == "stringcut":
        for field in step.findall("./fields/field"):
            output_name = (
                _direct_text(field, "out_stream_name")
                or _direct_text(field, "in_stream_name")
            )

            if output_name.casefold() == field_name.casefold():
                input_field = _direct_text(field, "in_stream_name")
                transformation_detail = "CORTE_TEXTO"
                break

    elif normalized_type == "selectvalues":
        for metadata in step.findall("./fields/meta"):
            output_name = (
                _direct_text(metadata, "rename")
                or _direct_text(metadata, "name")
            )

            if output_name.casefold() == field_name.casefold():
                input_field = _direct_text(metadata, "name") or field_name
                transformation_detail = "FORMATEO_CAMPO"
                break

    elif normalized_type == "replacestring":
        for replacement in step.findall("./fields/field"):
            source_name = _direct_text(replacement, "in_stream_name")
            output_name = (
                _direct_text(replacement, "out_stream_name")
                or source_name
            )

            if output_name.casefold() == field_name.casefold():
                input_field = source_name or field_name
                transformation_detail = "REEMPLAZO_TEXTO"
                break

    elif normalized_type == "ifnull":
        selected_fields = {
            _direct_text(field, "name").casefold()
            for field in step.findall("./fields/field")
        }

        if field_name.casefold() in selected_fields:
            transformation_detail = "REEMPLAZA_NULO"

    origin = _trace_predecessors(
        current_step=step_name,
        field_name=input_field,
        steps=steps,
        predecessors=predecessors,
        visited=visited,
    )

    if transformation_detail:
        return _merge_origins(
            [origin],
            detail=origin["detalle"],
            step={**step_record, "operacion": transformation_detail},
        )

    return _merge_origins([origin], step=step_record)


def _trace_predecessors(
    *,
    current_step: str,
    field_name: str,
    steps: dict[str, ET.Element],
    predecessors: dict[str, list[str]],
    visited: set[tuple[str, str]],
) -> dict[str, Any]:
    origins = [
        _trace_field(
            step_name=predecessor,
            field_name=field_name,
            steps=steps,
            predecessors=predecessors,
            visited=visited,
        )
        for predecessor in predecessors.get(current_step, [])
    ]

    return _merge_origins(origins) if origins else _empty_origin(field_name)


def trace_fact_fields(
    transformation: dict[str, Any],
    output_step: str,
) -> list[dict[str, Any]]:
    """Reconstruye el origen inmediato y técnico de cada columna FACT."""

    xml_element = transformation.get("xml_element")

    if not isinstance(xml_element, ET.Element):
        return []

    steps = {
        _direct_text(step, "name"): step
        for step in xml_element.findall("./step")
        if _direct_text(step, "name")
    }
    target = steps.get(output_step)

    if target is None:
        return []

    predecessors = _predecessor_index(xml_element)
    lineage: list[dict[str, Any]] = []

    for field in target.findall("./fields/field"):
        destination = (
            _direct_text(field, "column_name")
            or _direct_text(field, "name")
            or _direct_text(field, "field_name")
        )
        stream_field = (
            _direct_text(field, "stream_name")
            or _direct_text(field, "field_name")
            or _direct_text(field, "name")
        )

        if not destination:
            continue

        origin = _trace_predecessors(
            current_step=output_step,
            field_name=stream_field or destination,
            steps=steps,
            predecessors=predecessors,
            visited=set(),
        )
        lineage.append(
            {
                "campo_fact": destination,
                "campo_flujo": stream_field,
                **origin,
            }
        )

    return lineage


def trace_dimension_fields(
    transformation: dict[str, Any],
    dimension_step: str,
) -> list[dict[str, Any]]:
    """Rastrea cada clave o atributo escrito por un DimensionLookup."""

    xml_element = transformation.get("xml_element")

    if not isinstance(xml_element, ET.Element):
        return []

    steps = {
        _direct_text(step, "name"): step
        for step in xml_element.findall("./step")
        if _direct_text(step, "name")
    }
    target = steps.get(dimension_step)

    if target is None:
        return []

    if _direct_text(target, "type").casefold() != "dimensionlookup":
        return trace_fact_fields(transformation, dimension_step)

    predecessors = _predecessor_index(xml_element)
    lineage: list[dict[str, Any]] = []

    for field_type in ("key", "field"):
        for field in target.findall(f"./fields/{field_type}"):
            stream_field = _direct_text(field, "name")
            destination = _direct_text(field, "lookup") or stream_field

            if not destination or not stream_field:
                continue

            origin = _trace_predecessors(
                current_step=dimension_step,
                field_name=stream_field,
                steps=steps,
                predecessors=predecessors,
                visited=set(),
            )
            lineage.append(
                {
                    "campo_dimension": destination,
                    "campo_flujo": stream_field,
                    "rol": (
                        "CLAVE_NATURAL"
                        if field_type == "key"
                        else "ATRIBUTO"
                    ),
                    **origin,
                }
            )

    return lineage
