from __future__ import annotations

from collections import deque
from typing import Any


MAX_NODOS_RUTA = 180


def _texto(valor: Any) -> str:
    return str(valor or "").strip()


def _normalizar(valor: Any) -> str:
    return _texto(valor).casefold()


def _id_nodo(
    tipo: str,
    nombre: str,
    proceso_id: str = "",
    componente: str = "",
) -> str:
    partes = [
        tipo.upper(),
        proceso_id,
        componente,
        nombre,
    ]
    return "|".join(_normalizar(parte) for parte in partes)


def _crear_nodo(
    nodos: dict[str, dict[str, Any]],
    *,
    tipo: str,
    nombre: str,
    proceso_id: str = "",
    proceso: str = "",
    componente: str = "",
    estado: str = "resuelto",
) -> str:
    identificador = _id_nodo(
        tipo,
        nombre,
        proceso_id,
        componente,
    )

    nodos.setdefault(
        identificador,
        {
            "id": identificador,
            "tipo": tipo.upper(),
            "nombre": nombre or "Unknown",
            "proceso_id": proceso_id,
            "proceso": proceso,
            "componente": componente,
            "estado": estado or "resuelto",
        },
    )

    return identificador


def _nodo_por_lado(
    nodos: dict[str, dict[str, Any]],
    transicion: dict[str, Any],
    *,
    lado: str,
) -> str:
    es_origen = lado == "origen"
    tipo = _texto(
        transicion.get(
            "origen_tipo" if es_origen else "destino_tipo",
            "VARIABLE",
        )
    ).upper()
    nombre = _texto(
        transicion.get(
            "variable_origen" if es_origen else "variable_destino"
        )
    )
    proceso_id = _texto(
        transicion.get(
            "origen_proceso_id" if es_origen else "destino_proceso_id"
        )
        or transicion.get("proceso_id")
    )
    proceso = _texto(
        transicion.get(
            "proceso" if es_origen else "proceso_destino"
        )
        or transicion.get("proceso")
    )
    componente = _texto(transicion.get("componente"))

    if tipo in {"CAMPO_FLUJO", "DINAMICO"}:
        return _crear_nodo(
            nodos,
            tipo=tipo,
            nombre=nombre,
            proceso_id=proceso_id,
            proceso=proceso,
            componente=componente,
            estado=_texto(transicion.get("estado")),
        )

    if tipo == "PARAMETRO":
        return _crear_nodo(
            nodos,
            tipo="PARAMETRO",
            nombre=nombre,
            proceso_id=proceso_id,
            proceso=proceso,
            componente="parameters",
            estado=_texto(transicion.get("estado")),
        )

    return _crear_nodo(
        nodos,
        tipo=tipo or "VARIABLE",
        nombre=nombre,
        proceso_id="" if tipo == "VARIABLE" else proceso_id,
        proceso=proceso,
        componente="" if tipo == "VARIABLE" else componente,
        estado=_texto(transicion.get("estado")),
    )


def _agregar_arista(
    aristas: list[dict[str, Any]],
    *,
    origen: str,
    destino: str,
    evento: str,
    proceso: str = "",
    componente: str = "",
    estado: str = "resuelto",
) -> None:
    if not origen or not destino or origen == destino:
        return

    clave = (
        origen,
        destino,
        evento,
        proceso,
        componente,
    )

    if any(arista.get("_clave") == clave for arista in aristas):
        return

    aristas.append(
        {
            "id": f"lineage-edge-{len(aristas) + 1}",
            "origen": origen,
            "destino": destino,
            "evento": evento or "FLOW",
            "proceso": proceso,
            "componente": componente,
            "estado": estado or "resuelto",
            "_clave": clave,
        }
    )


def _construir_grafo_base(
    variables_modelo: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    nodos: dict[str, dict[str, Any]] = {}
    aristas: list[dict[str, Any]] = []

    transiciones = variables_modelo.get("transiciones", [])
    if not isinstance(transiciones, list):
        transiciones = []

    for transicion in transiciones:
        if not isinstance(transicion, dict):
            continue

        tipo_evento = _texto(
            transicion.get("tipo_evento")
        ).upper()

        if tipo_evento == "GET_VARIABLE":
            origen = _crear_nodo(
                nodos,
                tipo="VARIABLE",
                nombre=_texto(
                    transicion.get("variable_origen")
                ),
            )
            destino = _crear_nodo(
                nodos,
                tipo="CAMPO_FLUJO",
                nombre=_texto(
                    transicion.get("variable_destino")
                ),
                proceso_id=_texto(
                    transicion.get("proceso_id")
                ),
                proceso=_texto(transicion.get("proceso")),
                componente=_texto(
                    transicion.get("componente")
                ),
            )

        elif tipo_evento in {
            "RESULTADO_SQL",
            "ORIGEN_DINAMICO",
        }:
            origen = _crear_nodo(
                nodos,
                tipo="DINAMICO",
                nombre=_texto(
                    transicion.get("variable_origen")
                ),
                proceso_id=_texto(
                    transicion.get("proceso_id")
                ),
                proceso=_texto(transicion.get("proceso")),
                componente=_texto(
                    transicion.get("componente")
                ),
            )
            destino = _crear_nodo(
                nodos,
                tipo="CAMPO_FLUJO",
                nombre=_texto(
                    transicion.get("variable_destino")
                ),
                proceso_id=_texto(
                    transicion.get("proceso_id")
                ),
                proceso=_texto(transicion.get("proceso")),
                componente=_texto(
                    transicion.get("componente")
                ),
            )

        elif tipo_evento == "SET_VARIABLE":
            origen = _crear_nodo(
                nodos,
                tipo="CAMPO_FLUJO",
                nombre=_texto(
                    transicion.get("variable_origen")
                ),
                proceso_id=_texto(
                    transicion.get("proceso_id")
                ),
                proceso=_texto(transicion.get("proceso")),
                componente=_texto(
                    transicion.get("componente")
                ),
            )
            destino = _crear_nodo(
                nodos,
                tipo="VARIABLE",
                nombre=_texto(
                    transicion.get("variable_destino")
                ),
            )

        else:
            origen = _nodo_por_lado(
                nodos,
                transicion,
                lado="origen",
            )
            destino = _nodo_por_lado(
                nodos,
                transicion,
                lado="destino",
            )

        _agregar_arista(
            aristas,
            origen=origen,
            destino=destino,
            evento=tipo_evento,
            proceso=_texto(transicion.get("proceso")),
            componente=_texto(
                transicion.get("componente")
            ),
            estado=_texto(transicion.get("estado")),
        )

    asignaciones = variables_modelo.get(
        "asignaciones_a_parametros",
        [],
    )
    if not isinstance(asignaciones, list):
        asignaciones = []

    for asignacion in asignaciones:
        if not isinstance(asignacion, dict):
            continue

        nombre_variable = _texto(
            asignacion.get("variable")
            or asignacion.get("origen")
        )
        parametro = _texto(
            asignacion.get("parametro_destino")
        )

        if not nombre_variable or not parametro:
            continue

        origen = _crear_nodo(
            nodos,
            tipo="VARIABLE",
            nombre=nombre_variable,
        )
        destino = _crear_nodo(
            nodos,
            tipo="PARAMETRO",
            nombre=parametro,
            proceso_id=_texto(
                asignacion.get("proceso_destino_id")
            ),
            proceso=_texto(
                asignacion.get("proceso_destino")
            ),
            componente="parameters",
            estado=_texto(asignacion.get("estado")),
        )

        _agregar_arista(
            aristas,
            origen=origen,
            destino=destino,
            evento="VARIABLE_ASIGNA_PARAMETRO",
            proceso=_texto(
                asignacion.get("proceso_origen")
            ),
            componente=_texto(
                asignacion.get("entrada")
            ),
            estado=_texto(asignacion.get("estado")),
        )

    return nodos, aristas


def _seleccionar_componente(
    inicio: str,
    aristas: list[dict[str, Any]],
) -> tuple[set[str], bool]:
    adyacencia: dict[str, set[str]] = {}

    for arista in aristas:
        origen = arista["origen"]
        destino = arista["destino"]
        adyacencia.setdefault(origen, set()).add(destino)
        adyacencia.setdefault(destino, set()).add(origen)

    visitados = {inicio}
    cola = deque([inicio])
    truncado = False

    while cola:
        actual = cola.popleft()

        for vecino in sorted(adyacencia.get(actual, set())):
            if vecino in visitados:
                continue

            if len(visitados) >= MAX_NODOS_RUTA:
                truncado = True
                break

            visitados.add(vecino)
            cola.append(vecino)

        if truncado:
            break

    return visitados, truncado


def _asignar_niveles(
    inicio: str,
    nodos_ids: set[str],
    aristas: list[dict[str, Any]],
) -> dict[str, int]:
    salientes: dict[str, list[str]] = {}
    entrantes: dict[str, list[str]] = {}

    for arista in aristas:
        origen = arista["origen"]
        destino = arista["destino"]

        if origen not in nodos_ids or destino not in nodos_ids:
            continue

        salientes.setdefault(origen, []).append(destino)
        entrantes.setdefault(destino, []).append(origen)

    niveles: dict[str, int] = {inicio: 0}

    cola_aguas_arriba = deque([(inicio, 0)])
    visitados_arriba = {inicio}
    while cola_aguas_arriba:
        actual, distancia = cola_aguas_arriba.popleft()
        for anterior in entrantes.get(actual, []):
            if anterior in visitados_arriba:
                continue
            visitados_arriba.add(anterior)
            niveles[anterior] = -(distancia + 1)
            cola_aguas_arriba.append((anterior, distancia + 1))

    cola_aguas_abajo = deque([(inicio, 0)])
    visitados_abajo = {inicio}
    while cola_aguas_abajo:
        actual, distancia = cola_aguas_abajo.popleft()
        for siguiente in salientes.get(actual, []):
            if siguiente in visitados_abajo:
                continue
            visitados_abajo.add(siguiente)
            if siguiente not in niveles or niveles[siguiente] == 0:
                niveles[siguiente] = distancia + 1
            cola_aguas_abajo.append((siguiente, distancia + 1))

    for identificador in nodos_ids:
        niveles.setdefault(identificador, 0)

    return niveles


def _nodo_evento_original(
    nodos: dict[str, dict[str, Any]],
    identificador_original: Any,
    evento: dict[str, Any],
    *,
    destino: bool,
) -> str:
    partes = (
        list(identificador_original)
        if isinstance(identificador_original, (list, tuple))
        else []
    )
    tipo = _texto(partes[0] if partes else "VARIABLE").upper()

    if tipo == "VARIABLE":
        nombre = _texto(
            partes[1] if len(partes) > 1 else (
                evento.get("variable_destino")
                if destino
                else evento.get("variable_origen")
            )
        )
        return _crear_nodo(
            nodos,
            tipo="VARIABLE",
            nombre=nombre,
        )

    if tipo == "PARAMETRO":
        proceso_id = _texto(partes[1] if len(partes) > 1 else "")
        nombre = _texto(
            partes[2] if len(partes) > 2 else evento.get("variable_destino")
        )
        return _crear_nodo(
            nodos,
            tipo="PARAMETRO",
            nombre=nombre,
            proceso_id=proceso_id,
            proceso=_texto(
                evento.get("proceso_destino")
                if destino
                else evento.get("proceso")
            ),
            componente="parameters",
            estado=_texto(evento.get("estado")),
        )

    if tipo == "CAMPO_FLUJO":
        proceso_id = _texto(partes[1] if len(partes) > 1 else "")
        componente = _texto(
            partes[2] if len(partes) > 2 else evento.get("componente")
        )
        nombre = _texto(
            partes[3] if len(partes) > 3 else (
                evento.get("variable_destino")
                if destino
                else evento.get("variable_origen")
            )
        )
        return _crear_nodo(
            nodos,
            tipo="CAMPO_FLUJO",
            nombre=nombre,
            proceso_id=proceso_id,
            proceso=_texto(evento.get("proceso")),
            componente=componente,
            estado=_texto(evento.get("estado")),
        )

    nombre = _texto(
        evento.get("variable_destino")
        if destino
        else evento.get("variable_origen")
    )
    return _crear_nodo(
        nodos,
        tipo=tipo,
        nombre=nombre or tipo.replace("_", " ").title(),
        proceso_id=_texto(evento.get("proceso_id")),
        proceso=_texto(evento.get("proceso")),
        componente=_texto(evento.get("componente")),
        estado=_texto(evento.get("estado")),
    )


def _construir_desde_linaje_original(
    nombre_variable: str,
    linaje: dict[str, Any],
) -> dict[str, Any]:
    nodos: dict[str, dict[str, Any]] = {}
    aristas: list[dict[str, Any]] = []
    inicio = _crear_nodo(
        nodos,
        tipo="VARIABLE",
        nombre=nombre_variable,
    )

    eventos = linaje.get("eventos", [])
    if not isinstance(eventos, list):
        eventos = []

    for indice, evento in enumerate(eventos):
        if not isinstance(evento, dict):
            continue

        tipo_evento = _texto(evento.get("tipo_evento")).upper()
        estado = _texto(evento.get("estado")) or "resuelto"
        proceso = _texto(evento.get("proceso"))
        componente = _texto(evento.get("componente"))

        if tipo_evento.startswith("ORIGEN_"):
            tipo_origen = tipo_evento.removeprefix("ORIGEN_")
            origen = _crear_nodo(
                nodos,
                tipo=tipo_origen,
                nombre={
                    "KETTLE": "kettle.properties",
                    "PARAMETRO": "Process parameter",
                    "INTERNO": "Internal value",
                    "NO_RESUELTO": "Origin not resolved",
                }.get(tipo_origen, tipo_origen.replace("_", " ").title()),
                proceso_id=f"origin-{indice}",
                proceso=proceso,
                componente=componente,
                estado=estado,
            )
            destino = inicio

        elif tipo_evento == "USO":
            origen = inicio
            destino = _crear_nodo(
                nodos,
                tipo="USO",
                nombre=componente or "Variable reference",
                proceso_id=_texto(evento.get("proceso_id")),
                proceso=proceso,
                componente=componente,
                estado=estado,
            )

        else:
            origen = _nodo_evento_original(
                nodos,
                evento.get("nodo_origen"),
                evento,
                destino=False,
            )
            destino = _nodo_evento_original(
                nodos,
                evento.get("nodo_destino"),
                evento,
                destino=True,
            )

        _agregar_arista(
            aristas,
            origen=origen,
            destino=destino,
            evento=tipo_evento,
            proceso=proceso,
            componente=componente,
            estado=estado,
        )

    ids_seleccionados = set(nodos)
    niveles = _asignar_niveles(inicio, ids_seleccionados, aristas)
    conteo_por_nivel: dict[int, int] = {}
    nodos_salida: list[dict[str, Any]] = []

    for identificador in sorted(
        ids_seleccionados,
        key=lambda nodo_id: (
            niveles.get(nodo_id, 0),
            nodos[nodo_id]["nombre"].casefold(),
        ),
    ):
        nivel = niveles.get(identificador, 0)
        orden = conteo_por_nivel.get(nivel, 0)
        conteo_por_nivel[nivel] = orden + 1
        nodos_salida.append(
            {
                **nodos[identificador],
                "nivel": nivel,
                "orden": orden,
                "seleccionado": identificador == inicio,
            }
        )

    for arista in aristas:
        arista.pop("_clave", None)

    procesos = [
        _texto(proceso)
        for proceso in linaje.get("procesos_involucrados", [])
        if _texto(proceso)
    ]

    return {
        "variable": nombre_variable,
        "etiqueta": f"${{{nombre_variable}}}",
        "origen": _texto(linaje.get("origen")) or "No resuelto",
        "estado": _texto(linaje.get("estado_origen")) or "no_resuelto",
        "truncado": False,
        "procesos": procesos,
        "total_procesos": len(procesos),
        "nodos": nodos_salida,
        "aristas": aristas,
        "total_nodos": len(nodos_salida),
        "total_aristas": len(aristas),
        "rutas_origen": linaje.get("rutas_origen", []),
        "avisos": linaje.get("avisos", []),
        "no_resueltos": linaje.get("no_resueltos", []),
        "fuente_algoritmo": "reconstructor_pentaho",
    }


def construir_ruta_variable(
    analisis_guardado: dict[str, Any],
    nombre_variable: str,
) -> dict[str, Any]:
    resultado = analisis_guardado.get("resultado", {})
    variables_modelo = resultado.get("variables", {})

    if not isinstance(variables_modelo, dict):
        raise ValueError(
            "El analisis no contiene informacion de variables."
        )

    resumen_variables = variables_modelo.get("resumen", [])
    if not isinstance(resumen_variables, list):
        resumen_variables = []

    resumen = next(
        (
            elemento
            for elemento in resumen_variables
            if isinstance(elemento, dict)
            and _normalizar(elemento.get("nombre"))
            == _normalizar(nombre_variable)
        ),
        None,
    )

    if resumen is None:
        raise ValueError(
            f"No se encontro la variable '{nombre_variable}'."
        )

    nombre_canonico = _texto(resumen.get("nombre"))

    linajes_originales = resultado.get("linaje_variables", {})
    if isinstance(linajes_originales, dict) and linajes_originales:
        linaje_original = next(
            (
                detalle
                for nombre, detalle in linajes_originales.items()
                if _normalizar(nombre) == _normalizar(nombre_canonico)
                and isinstance(detalle, dict)
            ),
            None,
        )
        if linaje_original is not None:
            return _construir_desde_linaje_original(
                nombre_canonico,
                linaje_original,
            )

    raise ValueError(
        "Este analisis fue generado antes de incorporar el linaje "
        "original. Vuelva a cargar y reconstruir el proceso una vez."
    )

    nodos, aristas = _construir_grafo_base(variables_modelo)
    inicio = _id_nodo("VARIABLE", nombre_canonico)

    inicio_existia = inicio in nodos

    if inicio not in nodos:
        _crear_nodo(
            nodos,
            tipo="VARIABLE",
            nombre=nombre_canonico,
        )

    if not inicio_existia:
        nodos_homonimos = [
            nodo_id
            for nodo_id, nodo in nodos.items()
            if nodo_id != inicio
            and _normalizar(nodo.get("nombre"))
            == _normalizar(nombre_canonico)
        ]

        for nodo_homonimo in nodos_homonimos:
            nodo = nodos[nodo_homonimo]
            _agregar_arista(
                aristas,
                origen=nodo_homonimo,
                destino=inicio,
                evento="CAMPO_IDENTIFICADO",
                proceso=_texto(nodo.get("proceso")),
                componente=_texto(nodo.get("componente")),
                estado=_texto(nodo.get("estado")),
            )

    referencias = variables_modelo.get("referencias", [])
    if not isinstance(referencias, list):
        referencias = []

    for referencia in referencias:
        if (
            not isinstance(referencia, dict)
            or _normalizar(referencia.get("nombre"))
            != _normalizar(nombre_canonico)
        ):
            continue

        proceso_id = _texto(referencia.get("proceso_id"))
        proceso = _texto(referencia.get("proceso"))
        componente = _texto(
            referencia.get("componente")
            or referencia.get("contexto_xml")
        )
        uso = _crear_nodo(
            nodos,
            tipo="USO",
            nombre=componente or "Variable reference",
            proceso_id=proceso_id,
            proceso=proceso,
            componente=componente,
            estado="resuelto",
        )

        _agregar_arista(
            aristas,
            origen=inicio,
            destino=uso,
            evento="USO",
            proceso=proceso,
            componente=componente,
        )

    if not any(
        arista["destino"] == inicio
        for arista in aristas
    ):
        origen_no_resuelto = _crear_nodo(
            nodos,
            tipo="NO_RESUELTO",
            nombre="Origin not resolved",
            componente="Static analysis",
            estado="no_resuelto",
        )

        _agregar_arista(
            aristas,
            origen=origen_no_resuelto,
            destino=inicio,
            evento="ORIGEN_NO_RESUELTO",
            componente="Static analysis",
            estado="no_resuelto",
        )

    ids_seleccionados, truncado = _seleccionar_componente(
        inicio,
        aristas,
    )

    aristas_seleccionadas = [
        arista
        for arista in aristas
        if arista["origen"] in ids_seleccionados
        and arista["destino"] in ids_seleccionados
    ]

    niveles = _asignar_niveles(
        inicio,
        ids_seleccionados,
        aristas_seleccionadas,
    )

    nodos_salida = []
    conteo_por_nivel: dict[int, int] = {}

    for identificador in sorted(
        ids_seleccionados,
        key=lambda nodo_id: (
            niveles.get(nodo_id, 0),
            nodos[nodo_id]["nombre"].casefold(),
        ),
    ):
        nivel = niveles.get(identificador, 0)
        orden = conteo_por_nivel.get(nivel, 0)
        conteo_por_nivel[nivel] = orden + 1

        nodos_salida.append(
            {
                **nodos[identificador],
                "nivel": nivel,
                "orden": orden,
                "seleccionado": identificador == inicio,
            }
        )

    for arista in aristas_seleccionadas:
        arista.pop("_clave", None)

    procesos = sorted(
        {
            _texto(nodo.get("proceso"))
            for nodo in nodos_salida
            if _texto(nodo.get("proceso"))
        },
        key=str.casefold,
    )

    tipos_origen = {
        nodo["tipo"]
        for nodo in nodos_salida
        if nodo["nivel"] < 0
    }

    if "NO_RESUELTO" in tipos_origen:
        origen = "Unresolved"
    elif "DINAMICO" in tipos_origen:
        origen = "Dynamic result"
    elif "PARAMETRO" in tipos_origen:
        origen = "Process parameter"
    elif any(nodo["nivel"] < 0 for nodo in nodos_salida):
        origen = "Internal process"
    else:
        origen = "Unresolved"

    return {
        "variable": nombre_canonico,
        "etiqueta": f"${{{nombre_canonico}}}",
        "origen": origen,
        "estado": (
            "no_resuelto"
            if origen == "Unresolved"
            else "parcial"
            if truncado
            else "reconstruido"
        ),
        "truncado": truncado,
        "procesos": procesos,
        "total_procesos": len(procesos),
        "nodos": nodos_salida,
        "aristas": aristas_seleccionadas,
        "total_nodos": len(nodos_salida),
        "total_aristas": len(aristas_seleccionadas),
    }
