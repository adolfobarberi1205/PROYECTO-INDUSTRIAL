from __future__ import annotations

from collections import deque
from hashlib import sha256
from typing import Any


class InvalidProcessAnalysisError(ValueError):
    """El resultado temporal no contiene un catalogo documentable."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(*parts: Any) -> str:
    payload = "\x1f".join(_text(part) for part in parts)
    return sha256(payload.encode("utf-8")).hexdigest()


def _as_dictionary(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _calculate_depths(
    root_process_key: str,
    relations: list[dict[str, Any]],
) -> dict[str, int]:
    adjacency: dict[str, set[str]] = {}

    for relation in relations:
        source = _text(relation.get("origen_id"))
        target = _text(relation.get("destino_id"))

        if source and target:
            adjacency.setdefault(source, set()).add(target)

    depths = {root_process_key: 0}
    pending: deque[str] = deque([root_process_key])

    while pending:
        source = pending.popleft()
        child_depth = depths[source] + 1

        for target in adjacency.get(source, set()):
            previous_depth = depths.get(target)

            if previous_depth is None or child_depth < previous_depth:
                depths[target] = child_depth
                pending.append(target)

    return depths


def build_process_persistence_payload(
    analysis: dict[str, Any],
    repository_name: str,
    process_name: str | None = None,
    repository_description: str | None = None,
) -> dict[str, Any]:
    """
    Convierte un resultado temporal sanitizado en registros permanentes.

    No abre una conexion ni escribe en PostgreSQL. Variables, parametros y
    linajes se excluyen deliberadamente de esta primera etapa.
    """

    normalized_repository_name = repository_name.strip().casefold()
    functional_process_name = _text(process_name)

    if not normalized_repository_name:
        raise InvalidProcessAnalysisError(
            "El repositorio necesita un nombre para ser documentado."
        )

    result = _as_dictionary(analysis.get("resultado"))
    nodes = _as_dictionary(result.get("nodos"))
    relations = [
        item
        for item in _as_list(result.get("relaciones"))
        if isinstance(item, dict)
    ]
    diagrams = _as_dictionary(result.get("diagramas"))
    root_process_key = _text(
        result.get("diagrama_principal_id")
        or result.get("job_principal_id")
    )

    if not nodes:
        raise InvalidProcessAnalysisError(
            "El analisis no contiene procesos reconstruidos."
        )

    if not root_process_key or root_process_key not in nodes:
        raise InvalidProcessAnalysisError(
            "No se encontro el proceso raiz dentro del catalogo reconstruido."
        )

    process_records: list[dict[str, Any]] = []

    for source_key, node_value in nodes.items():
        node = _as_dictionary(node_value)
        process_type = _text(node.get("tipo")).upper()

        if process_type not in {"JOB", "TRANSFORMATION"}:
            raise InvalidProcessAnalysisError(
                f"El proceso '{source_key}' tiene un tipo no soportado."
            )

        process_records.append(
            {
                "source_key": _text(source_key),
                "name": _text(node.get("nombre")),
                "original_name": _text(node.get("nombre_original")) or None,
                "normalized_name": (
                    _text(node.get("nombre_normalizado"))
                    or _text(node.get("nombre")).casefold()
                ),
                "process_type": process_type,
                "path": _text(node.get("ruta")),
                "original_path": _text(node.get("ruta_original")) or None,
                "normalized_path": (
                    _text(node.get("ruta_normalizada"))
                    or _text(node.get("ruta")).casefold()
                ),
                "full_path": _text(node.get("ruta_completa")) or None,
                "internal_identifier": (
                    _text(node.get("identificador_interno")) or None
                ),
            }
        )

    depths = _calculate_depths(root_process_key, relations)
    scope_records = [
        {
            "process_source_key": process["source_key"],
            "is_root": process["source_key"] == root_process_key,
            "depth": depths.get(process["source_key"]),
        }
        for process in process_records
    ]

    relation_records: list[dict[str, Any]] = []

    for relation in relations:
        source_process_key = _text(relation.get("origen_id"))
        target_process_key = _text(relation.get("destino_id")) or None
        entry_name = _text(relation.get("entrada"))

        if not source_process_key or source_process_key not in nodes:
            continue

        relation_records.append(
            {
                "source_key": _stable_hash(
                    source_process_key,
                    target_process_key,
                    entry_name,
                    relation.get("estado"),
                    relation.get("referencia_original"),
                ),
                "source_process_key": source_process_key,
                "target_process_key": (
                    target_process_key
                    if target_process_key in nodes
                    else None
                ),
                "entry_name": entry_name,
                "status": _text(relation.get("estado")) or "UNKNOWN",
                "original_reference": (
                    _text(relation.get("referencia_original")) or None
                ),
                "resolution_criterion": (
                    _text(relation.get("criterio_resolucion")) or None
                ),
                "reason_code": _text(relation.get("motivo_codigo")) or None,
                "target_name_snapshot": (
                    _text(relation.get("destino")) or None
                ),
                "target_path_snapshot": (
                    _text(relation.get("destino_ruta")) or None
                ),
                "target_type_snapshot": (
                    _text(relation.get("destino_tipo")) or None
                ),
            }
        )

    diagram_records: list[dict[str, Any]] = []

    for process_source_key, diagram_value in diagrams.items():
        if process_source_key not in nodes:
            continue

        diagram = _as_dictionary(diagram_value)
        element_records: list[dict[str, Any]] = []

        for index, element_value in enumerate(
            _as_list(diagram.get("elementos"))
        ):
            element = _as_dictionary(element_value)
            target = _as_dictionary(element.get("target"))
            target_process_key = _text(target.get("id")) or None
            x = float(element.get("x") or 0)
            y = float(element.get("y") or 0)

            element_records.append(
                {
                    "source_key": (
                        _text(element.get("id"))
                        or _stable_hash(
                            process_source_key,
                            "element",
                            index,
                            element.get("nombre"),
                        )
                    ),
                    "name": _text(element.get("nombre")),
                    "element_type": (
                        _text(element.get("tipo")) or "UNKNOWN"
                    ),
                    "category": (
                        _text(element.get("categoria")) or "UNKNOWN"
                    ),
                    "source_x": x,
                    "source_y": y,
                    "display_x": x,
                    "display_y": y,
                    "enabled": bool(element.get("habilitado", True)),
                    "navigable": bool(element.get("navegable", False)),
                    "target_process_key": (
                        target_process_key
                        if target_process_key in nodes
                        else None
                    ),
                    "target_status": _text(target.get("estado")) or None,
                    "display_order": index,
                }
            )

        hop_records: list[dict[str, Any]] = []

        for index, hop_value in enumerate(_as_list(diagram.get("hops"))):
            hop = _as_dictionary(hop_value)
            origin_name = _text(hop.get("origen"))
            destination_name = _text(hop.get("destino"))

            hop_records.append(
                {
                    "source_key": _stable_hash(
                        process_source_key,
                        "hop",
                        index,
                        origin_name,
                        destination_name,
                    ),
                    "origin_name": origin_name,
                    "destination_name": destination_name,
                    "enabled": bool(hop.get("habilitado", True)),
                    "evaluation": _text(hop.get("evaluacion")) or None,
                    "unconditional": bool(hop.get("incondicional", False)),
                    "display_order": index,
                }
            )

        diagram_records.append(
            {
                "process_source_key": _text(process_source_key),
                "technical_detail": _as_dictionary(
                    diagram.get("detalle_tecnico")
                ),
                "recommended_width": int(
                    diagram.get("ancho_recomendado") or 900
                ),
                "recommended_height": int(
                    diagram.get("alto_recomendado") or 600
                ),
                "elements": element_records,
                "hops": hop_records,
            }
        )

    root_node = _as_dictionary(nodes[root_process_key])

    if not functional_process_name:
        functional_process_name = _text(root_node.get("nombre"))

    if not functional_process_name:
        raise InvalidProcessAnalysisError(
            "El proceso necesita un nombre funcional para ser documentado."
        )

    return {
        "repository": {
            "name": repository_name.strip(),
            "normalized_name": normalized_repository_name,
            "description": repository_description,
            "source_type": "PENTAHO",
        },
        "documented_root": {
            "root_process_source_key": root_process_key,
            "name": functional_process_name,
            "status": "ACTIVE",
        },
        "processes": process_records,
        "scope": scope_records,
        "relations": relation_records,
        "diagrams": diagram_records,
        "counts": {
            "processes": len(process_records),
            "relations": len(relation_records),
            "diagrams": len(diagram_records),
            "elements": sum(
                len(diagram["elements"]) for diagram in diagram_records
            ),
            "hops": sum(len(diagram["hops"]) for diagram in diagram_records),
        },
    }
