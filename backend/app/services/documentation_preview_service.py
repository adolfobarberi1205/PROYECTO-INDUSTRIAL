from __future__ import annotations

from collections import Counter
from typing import Any


def _dictionary(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def build_documentation_preview(
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """Construye la vista previa sin persistir datos en PostgreSQL."""

    analysis_info = _dictionary(analysis.get("analisis"))
    summary = _dictionary(analysis.get("resumen"))
    result = _dictionary(analysis.get("resultado"))
    nodes = _dictionary(result.get("nodos"))
    relations = [
        relation
        for relation in _list(result.get("relaciones"))
        if isinstance(relation, dict)
    ]
    diagrams = _dictionary(result.get("diagramas"))
    warehouse_tables = _dictionary(result.get("tablas_dwh"))
    fact_dimension_relations = _dictionary(
        result.get("relaciones_fact_dim")
    )
    dimension_construction = _dictionary(
        result.get("construccion_dimensiones")
    )
    root_id = str(
        result.get("diagrama_principal_id")
        or result.get("job_principal_id")
        or ""
    )
    root_node = _dictionary(nodes.get(root_id))

    if not nodes or not root_node:
        raise ValueError(
            "El analisis no contiene un proceso raiz documentable."
        )

    processes = []

    for process_id, node_value in nodes.items():
        node = _dictionary(node_value)
        diagram = _dictionary(diagrams.get(process_id))

        processes.append(
            {
                "id": process_id,
                "nombre": node.get("nombre", ""),
                "tipo": node.get("tipo", ""),
                "ruta": node.get("ruta", ""),
                "es_principal": process_id == root_id,
                "elementos": len(_list(diagram.get("elementos"))),
                "hops": len(_list(diagram.get("hops"))),
                "tiene_diagrama": bool(diagram),
            }
        )

    processes.sort(
        key=lambda process: (
            not process["es_principal"],
            process["tipo"] != "JOB",
            str(process["nombre"]).casefold(),
        )
    )

    relation_statuses = Counter(
        str(relation.get("estado") or "UNKNOWN")
        for relation in relations
    )
    total_elements = sum(
        len(_list(_dictionary(diagram).get("elementos")))
        for diagram in diagrams.values()
    )
    total_hops = sum(
        len(_list(_dictionary(diagram).get("hops")))
        for diagram in diagrams.values()
    )

    return {
        "modo": "preview",
        "guardado": False,
        "analisis": {
            "job_principal": analysis_info.get("job_principal", ""),
            "carpeta": analysis_info.get("carpeta", ""),
            "fecha_analisis": analysis_info.get("fecha_analisis"),
            "estado": analysis_info.get("estado", ""),
        },
        "proceso_principal": {
            "id": root_id,
            "nombre": root_node.get("nombre", ""),
            "tipo": root_node.get("tipo", "JOB"),
            "ruta": root_node.get("ruta", ""),
        },
        "resumen": {
            "procesos": int(summary.get("procesos") or len(processes)),
            "jobs": int(summary.get("jobs") or 0),
            "transformaciones": int(summary.get("transformaciones") or 0),
            "relaciones": int(summary.get("relaciones") or len(relations)),
            "diagramas": int(summary.get("diagramas") or len(diagrams)),
            "elementos": total_elements,
            "hops": total_hops,
        },
        "procesos": processes,
        "relaciones_por_estado": dict(sorted(relation_statuses.items())),
        "tablas_dwh": {
            "disponible": bool(warehouse_tables.get("disponible", False)),
            "total": int(warehouse_tables.get("total") or 0),
            "total_dimensiones": int(
                warehouse_tables.get("total_dimensiones") or 0
            ),
            "total_hechos": int(
                warehouse_tables.get("total_hechos") or 0
            ),
            "dimensiones": _list(warehouse_tables.get("dimensiones")),
            "hechos": _list(warehouse_tables.get("hechos")),
        },
        "relaciones_fact_dim": {
            "disponible": bool(
                fact_dimension_relations.get("disponible", False)
            ),
            "total_hechos": int(
                fact_dimension_relations.get("total_hechos") or 0
            ),
            "total_relaciones": int(
                fact_dimension_relations.get("total_relaciones") or 0
            ),
            "hechos_sin_dimensiones": int(
                fact_dimension_relations.get("hechos_sin_dimensiones") or 0
            ),
            "hechos": _list(fact_dimension_relations.get("hechos")),
        },
        "construccion_dimensiones": {
            "disponible": bool(
                dimension_construction.get("disponible", False)
            ),
            "total_dimensiones": int(
                dimension_construction.get("total_dimensiones") or 0
            ),
            "dimensiones": _list(
                dimension_construction.get("dimensiones")
            ),
        },
        "almacenamiento": {
            "incluye": [
                "Procesos",
                "Relaciones",
                "Diagramas",
                "Elementos y posiciones",
                "Hops",
                "Tablas DIM y FACT",
                "Campos y fuentes de datos",
                "Relaciones FACT-DIM",
                "Variables, usos y transiciones",
                "Parametros, usos y asignaciones",
            ],
            "excluye_por_ahora": [
                "XML original",
                "Valores sensibles",
                "Credenciales y conexiones",
            ],
        },
    }
