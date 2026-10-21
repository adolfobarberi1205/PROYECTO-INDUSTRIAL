from __future__ import annotations

from collections import deque
from hashlib import sha1
from typing import Any


MAXIMO_ARISTAS_LINAJE = 140
MAXIMA_PROFUNDIDAD_LINAJE = 8


def _lista_textos(valor: Any) -> list[str]:
    if not isinstance(valor, list):
        return []

    return sorted(
        {
            str(elemento).strip()
            for elemento in valor
            if str(elemento).strip()
        },
        key=str.casefold,
    )


def _claves_dimension(valor: Any) -> list[dict[str, str]]:
    if not isinstance(valor, list):
        return []

    claves: list[dict[str, str]] = []

    for elemento in valor:
        if not isinstance(elemento, dict):
            continue

        campo_flujo = str(
            elemento.get("campo_flujo", "")
        ).strip()
        campo_dimension = str(
            elemento.get("campo_dimension", "")
        ).strip()

        if not campo_flujo and not campo_dimension:
            continue

        clave = {
            "campo_flujo": campo_flujo,
            "campo_dimension": campo_dimension,
        }

        if clave not in claves:
            claves.append(clave)

    return claves


def _alcance_variable(
    nombre: str,
    transiciones: list[dict[str, Any]],
) -> str:
    alcances: set[str] = set()

    for transicion in transiciones:
        if not isinstance(transicion, dict):
            continue

        origen = str(
            transicion.get("variable_origen", "")
        ).strip()
        destino = str(
            transicion.get("variable_destino", "")
        ).strip()

        if nombre not in {origen, destino}:
            continue

        alcance = str(
            transicion.get("alcance", "")
        ).strip().upper()

        if alcance:
            alcances.add(alcance)

    if "JVM" in alcances:
        return "JVM"

    if "ROOT_JOB" in alcances:
        return "Root Job"

    return "Referenced"


def construir_catalogo_linaje(
    analisis_guardado: dict[str, Any],
) -> dict[str, Any]:
    """
    Prepara un catalogo seguro para la interfaz de linaje.

    No devuelve valores de variables, valores por defecto,
    contenido XML ni referencias originales.
    """

    resultado = analisis_guardado.get("resultado", {})
    variables_modelo = resultado.get("variables", {})
    parametros_modelo = resultado.get("parametros", {})

    if not isinstance(variables_modelo, dict):
        variables_modelo = {}

    if not isinstance(parametros_modelo, dict):
        parametros_modelo = {}

    transiciones_originales = variables_modelo.get(
        "transiciones",
        [],
    )
    transiciones = [
        elemento
        for elemento in transiciones_originales
        if isinstance(elemento, dict)
    ] if isinstance(transiciones_originales, list) else []

    variables: list[dict[str, Any]] = []

    resumen_variables = variables_modelo.get("resumen", [])
    if isinstance(resumen_variables, list):
        for elemento in resumen_variables:
            if not isinstance(elemento, dict):
                continue

            nombre = str(elemento.get("nombre", "")).strip()
            if not nombre:
                continue

            procesos = _lista_textos(
                elemento.get("procesos")
            )
            evidencias = _lista_textos(
                elemento.get("evidencias")
            )
            componentes = _lista_textos(
                elemento.get("componentes")
            )
            parametros_alimentados = _lista_textos(
                elemento.get("parametros_alimentados")
            )

            variables.append(
                {
                    "nombre": nombre,
                    "etiqueta": f"${{{nombre}}}",
                    "valor": "Not stored",
                    "alcance": _alcance_variable(
                        nombre,
                        transiciones,
                    ),
                    "procesos": procesos,
                    "usado_en": len(procesos),
                    "evidencias": evidencias,
                    "total_evidencias": len(evidencias),
                    "componentes": componentes,
                    "total_componentes": len(componentes),
                    "parametros_alimentados": (
                        parametros_alimentados
                    ),
                }
            )

    parametros: list[dict[str, Any]] = []

    resumen_parametros = parametros_modelo.get("resumen", [])
    if isinstance(resumen_parametros, list):
        for elemento in resumen_parametros:
            if not isinstance(elemento, dict):
                continue

            nombre = str(elemento.get("nombre", "")).strip()
            if not nombre:
                continue

            declarado_en = _lista_textos(
                elemento.get("declarado_en")
            )
            referenciado_en = _lista_textos(
                elemento.get("referenciado_en")
            )
            aparece_en_llamadas = _lista_textos(
                elemento.get("aparece_en_llamadas")
            )

            procesos = sorted(
                set(declarado_en + referenciado_en),
                key=str.casefold,
            )

            parametros.append(
                {
                    "nombre": nombre,
                    "etiqueta": nombre,
                    "declarado_en": declarado_en,
                    "total_declaraciones": len(declarado_en),
                    "referenciado_en": referenciado_en,
                    "total_referencias": len(referenciado_en),
                    "aparece_en_llamadas": aparece_en_llamadas,
                    "total_llamadas": len(aparece_en_llamadas),
                    "procesos": procesos,
                    "usado_en": len(procesos),
                }
            )

    variables.sort(
        key=lambda elemento: elemento["nombre"].casefold()
    )
    parametros.sort(
        key=lambda elemento: elemento["nombre"].casefold()
    )

    relaciones_fact_dim = resultado.get("relaciones_fact_dim", {})
    hechos_originales = (
        relaciones_fact_dim.get("hechos", [])
        if isinstance(relaciones_fact_dim, dict)
        else []
    )
    hechos: list[dict[str, Any]] = []

    for hecho in hechos_originales:
        if not isinstance(hecho, dict):
            continue

        campos = []

        for campo in hecho.get("linaje_campos", []):
            if not isinstance(campo, dict):
                continue

            nombre_campo = str(campo.get("campo_fact", "")).strip()

            if not nombre_campo:
                continue

            campos.append(
                {
                    "nombre": nombre_campo,
                    "campo_flujo": str(
                        campo.get("campo_flujo", "")
                    ).strip(),
                    "tipo_origen": str(
                        campo.get("tipo_origen", "NO_DETERMINADO")
                    ).strip(),
                    "detalle": str(campo.get("detalle", "")).strip(),
                    "campos_origen": _lista_textos(
                        campo.get("campos_origen")
                    ),
                    "tablas_origen": _lista_textos(
                        campo.get("tablas_origen")
                    ),
                    "fuentes_origen": _lista_textos(
                        campo.get("fuentes_origen")
                    ),
                    "claves_dimension": _claves_dimension(
                        campo.get("claves_dimension")
                    ),
                }
            )

        hechos.append(
            {
                "nombre": str(
                    hecho.get("nombre_resuelto")
                    or hecho.get("nombre", "")
                ).strip(),
                "tipo": "FACT",
                "total_campos": len(campos),
                "campos": campos,
                "dimensiones": _lista_textos(
                    [
                        dimension.get("nombre_resuelto")
                        or dimension.get("nombre", "")
                        for dimension in hecho.get("dimensiones", [])
                        if isinstance(dimension, dict)
                    ]
                ),
            }
        )

    construccion_dimensiones = resultado.get(
        "construccion_dimensiones",
        {},
    )
    dimensiones_originales = (
        construccion_dimensiones.get("dimensiones", [])
        if isinstance(construccion_dimensiones, dict)
        else []
    )
    dimensiones: list[dict[str, Any]] = []

    for dimension in dimensiones_originales:
        if not isinstance(dimension, dict):
            continue

        campos = []

        for campo in dimension.get("linaje_campos", []):
            if not isinstance(campo, dict):
                continue

            nombre_campo = str(
                campo.get("campo_dimension", "")
            ).strip()

            if not nombre_campo:
                continue

            campos.append(
                {
                    "nombre": nombre_campo,
                    "campo_flujo": str(
                        campo.get("campo_flujo", "")
                    ).strip(),
                    "rol": str(campo.get("rol", "ATRIBUTO")).strip(),
                    "tipo_origen": str(
                        campo.get("tipo_origen", "NO_DETERMINADO")
                    ).strip(),
                    "detalle": str(campo.get("detalle", "")).strip(),
                    "campos_origen": _lista_textos(
                        campo.get("campos_origen")
                    ),
                    "tablas_origen": _lista_textos(
                        campo.get("tablas_origen")
                    ),
                    "fuentes_origen": _lista_textos(
                        campo.get("fuentes_origen")
                    ),
                }
            )

        fuentes_originales = _lista_textos(
            [
                fuente.get("nombre", "")
                for fuente in dimension.get("fuentes_origen", [])
                if isinstance(fuente, dict)
            ]
        )

        if not fuentes_originales:
            fuentes_originales = _lista_textos(
                [
                    f"Excel: {paso.get('nombre', '')}"
                    for paso in dimension.get("pasos", [])
                    if isinstance(paso, dict)
                    and str(paso.get("tipo", "")).strip().casefold()
                    == "excelinput"
                    and str(paso.get("nombre", "")).strip()
                ]
            )

        dimensiones.append(
            {
                "nombre": str(
                    dimension.get("nombre_resuelto")
                    or dimension.get("nombre", "")
                ).strip(),
                "tipo": "DIM",
                "total_campos": len(campos),
                "campos": campos,
                "tablas_fuente": _lista_textos(
                    [
                        table.get("nombre", "")
                        for table in dimension.get("tablas_fuente", [])
                        if isinstance(table, dict)
                    ]
                ),
                "fuentes_origen": fuentes_originales,
            }
        )

    hechos.sort(key=lambda elemento: elemento["nombre"].casefold())
    dimensiones.sort(key=lambda elemento: elemento["nombre"].casefold())

    analisis = analisis_guardado.get("analisis", {})

    return {
        "analisis": {
            "job_principal": analisis.get("job_principal", ""),
            "carpeta": analisis.get("carpeta", ""),
        },
        "totales": {
            "variables": len(variables),
            "parametros": len(parametros),
            "tablas_fact": len(hechos),
            "tablas_dim": len(dimensiones),
        },
        "variables": variables,
        "parametros": parametros,
        "tablas": {
            "facts": hechos,
            "dimensions": dimensiones,
        },
        "politica_valores": (
            "Los valores de variables y parametros no se almacenan."
        ),
    }


def _normalizar_tipo_nodo(tipo: Any) -> str:
    texto = str(tipo or "").strip().upper()

    equivalencias = {
        "PARAMETER": "PARAMETRO",
        "PARAMETER_VALUE": "PARAMETRO",
        "FLOW_FIELD": "CAMPO_FLUJO",
        "FIELD": "CAMPO_FLUJO",
        "DYNAMIC": "DINAMICO",
    }

    return equivalencias.get(texto, texto or "DESCONOCIDO")


def _identificador_nodo(
    tipo: str,
    nombre: str,
    proceso_id: str,
) -> str:
    proceso_clave = (
        "global"
        if tipo == "VARIABLE"
        else proceso_id or "sin_proceso"
    )
    firma = f"{tipo}|{proceso_clave}|{nombre.casefold()}"
    resumen = sha1(
        firma.encode("utf-8")
    ).hexdigest()[:16]

    return f"node-{resumen}"


def _crear_nodo_evento(
    nodos: dict[str, dict[str, Any]],
    tipo: Any,
    nombre: Any,
    proceso_id: Any,
    proceso: Any,
    componente: Any,
) -> str | None:
    tipo_normalizado = _normalizar_tipo_nodo(tipo)
    nombre_normalizado = str(nombre or "").strip()
    proceso_id_normalizado = str(
        proceso_id or ""
    ).strip()

    if not nombre_normalizado:
        return None

    identificador = _identificador_nodo(
        tipo_normalizado,
        nombre_normalizado,
        proceso_id_normalizado,
    )

    nodos.setdefault(
        identificador,
        {
            "id": identificador,
            "nombre": nombre_normalizado,
            "tipo": tipo_normalizado,
            "proceso_id": proceso_id_normalizado,
            "proceso": str(proceso or "").strip(),
            "componente": str(componente or "").strip(),
            "estado": "resuelto",
        },
    )

    return identificador


def _construir_grafo_transiciones(
    transiciones: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    nodos: dict[str, dict[str, Any]] = {}
    aristas: list[dict[str, Any]] = []

    for indice, transicion in enumerate(transiciones):
        if not isinstance(transicion, dict):
            continue

        proceso_id = str(
            transicion.get("proceso_id", "")
        ).strip()
        proceso = str(
            transicion.get("proceso", "")
        ).strip()
        componente = str(
            transicion.get("componente", "")
        ).strip()

        origen_id = _crear_nodo_evento(
            nodos=nodos,
            tipo=transicion.get("origen_tipo"),
            nombre=transicion.get("variable_origen"),
            proceso_id=(
                transicion.get("origen_proceso_id")
                or proceso_id
            ),
            proceso=proceso,
            componente=componente,
        )

        destino_id = _crear_nodo_evento(
            nodos=nodos,
            tipo=transicion.get("destino_tipo"),
            nombre=transicion.get("variable_destino"),
            proceso_id=(
                transicion.get("destino_proceso_id")
                or proceso_id
            ),
            proceso=(
                transicion.get("proceso_destino")
                or proceso
            ),
            componente=componente,
        )

        if not origen_id or not destino_id:
            continue

        aristas.append(
            {
                "id": f"edge-{indice}",
                "origen": origen_id,
                "destino": destino_id,
                "tipo_evento": str(
                    transicion.get(
                        "tipo_evento",
                        "DERIVACION",
                    )
                ).strip(),
                "proceso": proceso,
                "componente": componente,
                "estado": str(
                    transicion.get("estado", "resuelto")
                ).strip(),
                "alcance": str(
                    transicion.get("alcance", "")
                ).strip(),
            }
        )

    return nodos, aristas


def _seleccionar_subgrafo_variable(
    variable_id: str,
    aristas: list[dict[str, Any]],
) -> tuple[set[str], set[str], dict[str, int]]:
    entrantes: dict[str, list[dict[str, Any]]] = {}
    salientes: dict[str, list[dict[str, Any]]] = {}

    for arista in aristas:
        entrantes.setdefault(
            arista["destino"],
            [],
        ).append(arista)
        salientes.setdefault(
            arista["origen"],
            [],
        ).append(arista)

    nodos_seleccionados = {variable_id}
    aristas_seleccionadas: set[str] = set()
    niveles = {variable_id: 0}

    cola_entrada = deque(
        [(variable_id, 0)]
    )
    visitados_entrada = {variable_id}

    while cola_entrada:
        nodo_id, distancia = cola_entrada.popleft()

        if distancia >= MAXIMA_PROFUNDIDAD_LINAJE:
            continue

        for arista in entrantes.get(nodo_id, []):
            if len(aristas_seleccionadas) >= MAXIMO_ARISTAS_LINAJE:
                break

            aristas_seleccionadas.add(arista["id"])
            origen_id = arista["origen"]
            nodos_seleccionados.add(origen_id)
            niveles[origen_id] = min(
                niveles.get(origen_id, 0),
                -(distancia + 1),
            )

            if origen_id not in visitados_entrada:
                visitados_entrada.add(origen_id)
                cola_entrada.append(
                    (origen_id, distancia + 1)
                )

    cola_salida = deque(
        [(variable_id, 0)]
    )
    visitados_salida = {variable_id}

    while cola_salida:
        nodo_id, distancia = cola_salida.popleft()

        if distancia >= MAXIMA_PROFUNDIDAD_LINAJE:
            continue

        for arista in salientes.get(nodo_id, []):
            if len(aristas_seleccionadas) >= MAXIMO_ARISTAS_LINAJE:
                break

            aristas_seleccionadas.add(arista["id"])
            destino_id = arista["destino"]
            nodos_seleccionados.add(destino_id)
            niveles[destino_id] = max(
                niveles.get(destino_id, 0),
                distancia + 1,
            )

            if destino_id not in visitados_salida:
                visitados_salida.add(destino_id)
                cola_salida.append(
                    (destino_id, distancia + 1)
                )

    return (
        nodos_seleccionados,
        aristas_seleccionadas,
        niveles,
    )


def construir_diagrama_linaje_variable(
    analisis_guardado: dict[str, Any],
    nombre_variable: str,
) -> dict[str, Any]:
    """
    Reconstruye un diagrama causal con la informacion
    sanitizada que permanece despues de eliminar el XML.
    """

    nombre = str(nombre_variable or "").strip()

    if not nombre:
        raise ValueError("Debe indicar una variable.")

    resultado = analisis_guardado.get("resultado", {})
    informacion = resultado.get("variables", {})

    if not isinstance(informacion, dict):
        raise ValueError(
            "El analisis no contiene informacion de variables."
        )

    resumen_variables = informacion.get("resumen", [])
    variable_resumen = next(
        (
            elemento
            for elemento in resumen_variables
            if isinstance(elemento, dict)
            and str(elemento.get("nombre", "")).strip().casefold()
            == nombre.casefold()
        ),
        None,
    ) if isinstance(resumen_variables, list) else None

    if variable_resumen is None:
        raise ValueError(
            f"No se encontro la variable '{nombre}'."
        )

    transiciones_originales = informacion.get(
        "transiciones",
        [],
    )
    transiciones = [
        elemento
        for elemento in transiciones_originales
        if isinstance(elemento, dict)
    ] if isinstance(transiciones_originales, list) else []

    nodos, aristas = _construir_grafo_transiciones(
        transiciones
    )

    variable_id = _identificador_nodo(
        "VARIABLE",
        nombre,
        "",
    )
    nodos.setdefault(
        variable_id,
        {
            "id": variable_id,
            "nombre": nombre,
            "tipo": "VARIABLE",
            "proceso_id": "",
            "proceso": "",
            "componente": "",
            "estado": "resuelto",
        },
    )

    (
        ids_nodos,
        ids_aristas,
        niveles,
    ) = _seleccionar_subgrafo_variable(
        variable_id,
        aristas,
    )

    aristas_diagrama = [
        arista
        for arista in aristas
        if arista["id"] in ids_aristas
    ]

    referencias_originales = informacion.get(
        "referencias",
        [],
    )
    referencias = [
        referencia
        for referencia in referencias_originales
        if isinstance(referencia, dict)
        and str(
            referencia.get("nombre", "")
        ).strip().casefold() == nombre.casefold()
    ] if isinstance(referencias_originales, list) else []

    usos_agrupados: dict[
        tuple[str, str],
        dict[str, Any],
    ] = {}

    for referencia in referencias:
        proceso = str(
            referencia.get("proceso", "")
        ).strip()
        componente = str(
            referencia.get("componente", "")
        ).strip()
        clave = (proceso, componente)
        usos_agrupados.setdefault(
            clave,
            referencia,
        )

    nivel_uso = max(
        [nivel for nivel in niveles.values() if nivel >= 0],
        default=0,
    ) + 1

    for indice, referencia in enumerate(
        list(usos_agrupados.values())[:60],
        start=1,
    ):
        proceso = str(
            referencia.get("proceso", "")
        ).strip()
        componente = str(
            referencia.get("componente", "")
            or referencia.get("contexto_xml", "")
            or "Variable reference"
        ).strip()
        uso_nombre = proceso or componente or f"Use {indice}"
        uso_id = _identificador_nodo(
            "USO",
            f"{uso_nombre}|{componente}|{indice}",
            str(referencia.get("proceso_id", "")),
        )
        nodos[uso_id] = {
            "id": uso_id,
            "nombre": uso_nombre,
            "tipo": "USO",
            "proceso_id": str(
                referencia.get("proceso_id", "")
            ).strip(),
            "proceso": proceso,
            "componente": componente,
            "estado": "resuelto",
        }
        ids_nodos.add(uso_id)
        niveles[uso_id] = nivel_uso
        aristas_diagrama.append(
            {
                "id": f"use-edge-{indice}",
                "origen": variable_id,
                "destino": uso_id,
                "tipo_evento": "USO",
                "proceso": proceso,
                "componente": componente,
                "estado": "resuelto",
                "alcance": "",
            }
        )

    tiene_origen = any(
        arista["destino"] == variable_id
        for arista in aristas_diagrama
    )

    if not tiene_origen:
        origen_id = _identificador_nodo(
            "NO_RESUELTO",
            f"Origen de {nombre}",
            "",
        )
        nodos[origen_id] = {
            "id": origen_id,
            "nombre": "Origin not resolved",
            "tipo": "NO_RESUELTO",
            "proceso_id": "",
            "proceso": "",
            "componente": "Static analysis",
            "estado": "no_resuelto",
        }
        ids_nodos.add(origen_id)
        niveles[origen_id] = -1
        aristas_diagrama.insert(
            0,
            {
                "id": "unresolved-origin-edge",
                "origen": origen_id,
                "destino": variable_id,
                "tipo_evento": "ORIGEN_NO_RESUELTO",
                "proceso": "",
                "componente": "Static analysis",
                "estado": "no_resuelto",
                "alcance": "",
            },
        )

    niveles_ordenados: dict[int, list[str]] = {}
    for nodo_id in ids_nodos:
        niveles_ordenados.setdefault(
            niveles.get(nodo_id, 0),
            [],
        ).append(nodo_id)

    nivel_minimo = min(
        niveles_ordenados,
        default=0,
    )
    nodos_diagrama: list[dict[str, Any]] = []

    for nivel in sorted(niveles_ordenados):
        ids_nivel = sorted(
            niveles_ordenados[nivel],
            key=lambda nodo_id: (
                str(nodos[nodo_id].get("proceso", "")).casefold(),
                str(nodos[nodo_id].get("nombre", "")).casefold(),
            ),
        )

        for posicion, nodo_id in enumerate(ids_nivel):
            nodo = nodos[nodo_id]
            nodos_diagrama.append(
                {
                    **nodo,
                    "nivel": nivel,
                    "x": 80 + (nivel - nivel_minimo) * 290,
                    "y": 70 + posicion * 126,
                    "seleccionado": nodo_id == variable_id,
                }
            )

    procesos = _lista_textos(
        variable_resumen.get("procesos")
    )

    return {
        "variable": {
            "nombre": nombre,
            "etiqueta": f"${{{nombre}}}",
            "procesos": procesos,
            "total_procesos": len(procesos),
        },
        "resumen": {
            "estado_origen": (
                "resuelto"
                if tiene_origen
                else "no_resuelto"
            ),
            "total_nodos": len(nodos_diagrama),
            "total_eventos": len(aristas_diagrama),
            "total_usos": len(usos_agrupados),
            "recorrido_limitado": (
                len(ids_aristas) >= MAXIMO_ARISTAS_LINAJE
                or len(usos_agrupados) > 60
            ),
        },
        "nodos": nodos_diagrama,
        "conexiones": aristas_diagrama,
    }
