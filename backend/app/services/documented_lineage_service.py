from __future__ import annotations

from collections import defaultdict, deque
from hashlib import sha1
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.documented_process_preview_service import (
    build_documented_process_preview,
)


def _unique(values: list[Any]) -> list[str]:
    return sorted(
        {str(value).strip() for value in values if str(value).strip()},
        key=str.casefold,
    )


def _field(item: dict[str, Any], fact: bool) -> dict[str, Any]:
    return {
        "nombre": item.get("campo_fact" if fact else "campo_dimension", ""),
        "campo_flujo": item.get("campo_flujo", ""),
        "rol": item.get("rol", "") if not fact else "",
        "tipo_origen": item.get("tipo_origen", "NO_DETERMINADO"),
        "detalle": item.get("detalle", ""),
        "campos_origen": _unique(item.get("campos_origen", [])),
        "tablas_origen": _unique(item.get("tablas_origen", [])),
        "fuentes_origen": _unique(item.get("fuentes_origen", [])),
        "claves_dimension": item.get("claves_dimension", []),
    }


def build_documented_lineage_catalog(
    session: Session,
    process_id: int,
) -> dict[str, Any]:
    """Devuelve el catalogo de linaje persistido con el contrato del front."""

    preview = build_documented_process_preview(session, process_id)

    variable_rows = session.execute(
        text(
            """
            SELECT variable.id, variable.nombre, variable.alcance,
                   usage.tipo_uso, usage.componente_nombre, usage.evidencia,
                   component.nombre AS componente
            FROM variable_etl AS variable
            LEFT JOIN uso_variable AS usage ON usage.variable_id = variable.id
            LEFT JOIN componente AS component ON component.id = usage.componente_id
            WHERE variable.proceso_id = :process_id
            ORDER BY variable.nombre, usage.orden, usage.id
            """
        ),
        {"process_id": process_id},
    ).mappings().all()

    parameter_links = session.execute(
        text(
            """
            SELECT assignment.variable_id, parameter.nombre
            FROM asignacion_variable_parametro AS assignment
            JOIN parametro_etl AS parameter ON parameter.id = assignment.parametro_id
            WHERE assignment.proceso_id = :process_id
            """
        ),
        {"process_id": process_id},
    ).mappings().all()
    links_by_variable: dict[int, list[str]] = defaultdict(list)
    for link in parameter_links:
        links_by_variable[int(link["variable_id"])].append(link["nombre"])

    grouped_variables: dict[int, dict[str, Any]] = {}
    for row in variable_rows:
        variable_id = int(row["id"])
        item = grouped_variables.setdefault(
            variable_id,
            {
                "nombre": row["nombre"],
                "etiqueta": f"${{{row['nombre']}}}",
                "valor": "Not stored",
                "alcance": row["alcance"] or "Referenced",
                "procesos": [],
                "evidencias": [],
                "componentes": [],
                "parametros_alimentados": links_by_variable[variable_id],
            },
        )
        component = row["componente_nombre"] or row["componente"] or ""
        if row["tipo_uso"] == "PROCESO" and component:
            item["procesos"].append(component)
        if row["evidencia"]:
            item["evidencias"].append(row["evidencia"])
        if component:
            item["componentes"].append(component)

    variables = []
    for item in grouped_variables.values():
        item["procesos"] = _unique(item["procesos"] or item["componentes"])
        item["evidencias"] = _unique(item["evidencias"])
        item["componentes"] = _unique(item["componentes"])
        item["parametros_alimentados"] = _unique(
            item["parametros_alimentados"]
        )
        item["usado_en"] = len(item["procesos"])
        item["total_evidencias"] = len(item["evidencias"])
        item["total_componentes"] = len(item["componentes"])
        variables.append(item)
    variables.sort(key=lambda item: item["nombre"].casefold())

    parameter_rows = session.execute(
        text(
            """
            SELECT parameter.id, parameter.nombre, usage.tipo_uso,
                   usage.componente_nombre, component.nombre AS componente
            FROM parametro_etl AS parameter
            LEFT JOIN uso_parametro AS usage ON usage.parametro_id = parameter.id
            LEFT JOIN componente AS component ON component.id = usage.componente_id
            WHERE parameter.proceso_id = :process_id
            ORDER BY parameter.nombre, usage.orden, usage.id
            """
        ),
        {"process_id": process_id},
    ).mappings().all()
    grouped_parameters: dict[int, dict[str, Any]] = {}
    for row in parameter_rows:
        item = grouped_parameters.setdefault(
            int(row["id"]),
            {
                "nombre": row["nombre"],
                "etiqueta": row["nombre"],
                "declarado_en": [],
                "referenciado_en": [],
                "aparece_en_llamadas": [],
            },
        )
        component = row["componente_nombre"] or row["componente"] or ""
        destination = {
            "DECLARADO": "declarado_en",
            "REFERENCIADO": "referenciado_en",
            "LLAMADA": "aparece_en_llamadas",
        }.get(row["tipo_uso"])
        if destination and component:
            item[destination].append(component)

    parameters = []
    for item in grouped_parameters.values():
        for key in ("declarado_en", "referenciado_en", "aparece_en_llamadas"):
            item[key] = _unique(item[key])
        item["total_declaraciones"] = len(item["declarado_en"])
        item["total_referencias"] = len(item["referenciado_en"])
        item["total_llamadas"] = len(item["aparece_en_llamadas"])
        item["procesos"] = _unique(
            item["declarado_en"] + item["referenciado_en"]
        )
        item["usado_en"] = len(item["procesos"])
        parameters.append(item)
    parameters.sort(key=lambda item: item["nombre"].casefold())

    facts = []
    for fact in preview["relaciones_fact_dim"]["hechos"]:
        fields = [_field(item, True) for item in fact["linaje_campos"]]
        facts.append(
            {
                "nombre": fact["nombre_resuelto"] or fact["nombre"],
                "tipo": "FACT",
                "total_campos": len(fields),
                "campos": fields,
                "dimensiones": _unique(
                    [
                        dimension["nombre_resuelto"] or dimension["nombre"]
                        for dimension in fact["dimensiones"]
                    ]
                ),
            }
        )

    dimensions = []
    for dimension in preview["construccion_dimensiones"]["dimensiones"]:
        fields = [_field(item, False) for item in dimension["linaje_campos"]]
        dimensions.append(
            {
                "nombre": dimension["nombre_resuelto"] or dimension["nombre"],
                "tipo": "DIM",
                "total_campos": len(fields),
                "campos": fields,
                "tablas_fuente": _unique(
                    [source["nombre"] for source in dimension["tablas_fuente"]]
                ),
                "fuentes_origen": [],
            }
        )

    return {
        "id_analisis": f"db-{process_id}",
        "analisis": {
            "job_principal": preview["analisis"]["job_principal"],
            "carpeta": preview["analisis"]["carpeta"],
        },
        "totales": {
            "variables": len(variables),
            "parametros": len(parameters),
            "tablas_fact": len(facts),
            "tablas_dim": len(dimensions),
        },
        "variables": variables,
        "parametros": parameters,
        "tablas": {"facts": facts, "dimensions": dimensions},
        "politica_valores": (
            "Los valores de variables y parametros no se almacenan."
        ),
    }


def _node_id(kind: str, name: str, suffix: str = "") -> str:
    signature = f"{kind}|{name.casefold()}|{suffix}"
    return f"db-node-{sha1(signature.encode('utf-8')).hexdigest()[:16]}"


def build_documented_variable_route(
    session: Session,
    process_id: int,
    variable_name: str,
) -> dict[str, Any]:
    name = variable_name.strip()
    if not name:
        raise ValueError("Debe indicar una variable.")

    selected = session.execute(
        text(
            """
            SELECT id, nombre FROM variable_etl
            WHERE proceso_id = :process_id AND lower(nombre) = lower(:name)
            """
        ),
        {"process_id": process_id, "name": name},
    ).mappings().one_or_none()
    if selected is None:
        raise FileNotFoundError(f"No se encontro la variable '{name}'.")

    rows = session.execute(
        text(
            """
            SELECT transition.id, transition.variable_origen_nombre,
                   transition.variable_destino_nombre,
                   transition.tipo_evento, transition.componente_nombre,
                   transition.detalle, transition.estado,
                   component.clave_origen AS proceso_id,
                   component.nombre AS proceso
            FROM transicion_variable AS transition
            LEFT JOIN componente AS component ON component.id = transition.componente_id
            WHERE transition.proceso_id = :process_id
            ORDER BY transition.orden, transition.id
            """
        ),
        {"process_id": process_id},
    ).mappings().all()

    adjacency: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        origin = (row["variable_origen_nombre"] or "").strip()
        destination = (row["variable_destino_nombre"] or "").strip()
        if origin and destination:
            adjacency[origin.casefold()].add(destination.casefold())
            adjacency[destination.casefold()].add(origin.casefold())

    connected = {name.casefold()}
    queue = deque([(name.casefold(), 0)])
    while queue:
        current, depth = queue.popleft()
        if depth >= 8:
            continue
        for neighbor in adjacency.get(current, set()):
            if neighbor not in connected:
                connected.add(neighbor)
                queue.append((neighbor, depth + 1))

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    selected_id = _node_id("VARIABLE", name)
    nodes[selected_id] = {
        "id": selected_id,
        "tipo": "VARIABLE",
        "nombre": name,
        "proceso_id": "",
        "proceso": "",
        "componente": "",
        "estado": "resuelto",
    }

    for row in rows:
        origin_name = (row["variable_origen_nombre"] or "").strip()
        destination_name = (row["variable_destino_nombre"] or "").strip()
        if not ({origin_name.casefold(), destination_name.casefold()} & connected):
            continue

        component = row["componente_nombre"] or row["proceso"] or "Proceso ETL"
        process_key = row["proceso_id"] or ""
        origin_id = (
            _node_id("VARIABLE", origin_name)
            if origin_name
            else _node_id("ORIGEN", component, str(row["id"]))
        )
        destination_id = (
            _node_id("VARIABLE", destination_name)
            if destination_name
            else _node_id("USO", component, str(row["id"]))
        )
        if origin_id == destination_id:
            destination_id = _node_id("USO", component, str(row["id"]))

        nodes.setdefault(
            origin_id,
            {
                "id": origin_id,
                "tipo": "VARIABLE" if origin_name else "ORIGEN",
                "nombre": origin_name or component,
                "proceso_id": process_key,
                "proceso": row["proceso"] or "",
                "componente": component,
                "estado": row["estado"] or "resuelto",
            },
        )
        nodes.setdefault(
            destination_id,
            {
                "id": destination_id,
                "tipo": "VARIABLE" if destination_name else "USO",
                "nombre": destination_name or component,
                "proceso_id": process_key,
                "proceso": row["proceso"] or "",
                "componente": component,
                "estado": row["estado"] or "resuelto",
            },
        )
        edges.append(
            {
                "id": f"db-edge-{row['id']}",
                "origen": origin_id,
                "destino": destination_id,
                "evento": row["tipo_evento"] or "TRANSICION",
                "proceso": row["proceso"] or "",
                "componente": component,
                "estado": row["estado"] or "resuelto",
            }
        )

    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["origen"]].append(edge["destino"])
        incoming[edge["destino"]].append(edge["origen"])
    levels = {selected_id: 0}
    for direction, sign in ((incoming, -1), (outgoing, 1)):
        queue_ids = deque([(selected_id, 0)])
        visited = {selected_id}
        while queue_ids:
            current, distance = queue_ids.popleft()
            for neighbor in direction.get(current, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                levels[neighbor] = sign * (distance + 1)
                queue_ids.append((neighbor, distance + 1))

    order_by_level: dict[int, int] = defaultdict(int)
    serialized_nodes = []
    for node in sorted(
        nodes.values(),
        key=lambda item: (levels.get(item["id"], 0), item["nombre"].casefold()),
    ):
        level = levels.get(node["id"], 0)
        order = order_by_level[level]
        order_by_level[level] += 1
        serialized_nodes.append(
            {
                **node,
                "nivel": level,
                "orden": order,
                "seleccionado": node["id"] == selected_id,
            }
        )

    processes = _unique([node["proceso"] for node in serialized_nodes])
    return {
        "id_analisis": f"db-{process_id}",
        "ruta": {
            "variable": name,
            "etiqueta": f"${{{name}}}",
            "origen": "Metadatos persistidos",
            "estado": "reconstruido",
            "truncado": False,
            "procesos": processes,
            "total_procesos": len(processes),
            "nodos": serialized_nodes,
            "aristas": edges,
            "total_nodos": len(serialized_nodes),
            "total_aristas": len(edges),
        },
    }
