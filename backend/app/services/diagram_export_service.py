from __future__ import annotations

import re
from typing import Any
import xml.etree.ElementTree as ET


VARIABLE_PATTERN = re.compile(r"\$\{([^}]+)\}")
IDENTIFIER = (
    r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\$\{[^}]+\}|[A-Za-z_][\w$#]*)'
)
QUALIFIED_IDENTIFIER = rf"{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER}){{0,2}}"
SOURCE_TABLE_PATTERN = re.compile(
    rf"\b(?:FROM|JOIN)\s+({QUALIFIED_IDENTIFIER})",
    re.IGNORECASE,
)
DESTINATION_TABLE_PATTERNS = (
    re.compile(rf"\bINSERT\s+INTO\s+({QUALIFIED_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"\bMERGE\s+INTO\s+({QUALIFIED_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"\bUPDATE\s+({QUALIFIED_IDENTIFIER})", re.IGNORECASE),
    re.compile(
        rf"\bCREATE\s+(?:GLOBAL\s+TEMPORARY\s+|TEMPORARY\s+)?TABLE\s+"
        rf"(?:IF\s+NOT\s+EXISTS\s+)?({QUALIFIED_IDENTIFIER})",
        re.IGNORECASE,
    ),
)


def _nombre_local(etiqueta: str) -> str:
    """
    Elimina un posible namespace de una etiqueta XML.
    """

    return etiqueta.rsplit("}", 1)[-1].lower()


def _buscar_hijo_directo(
    elemento: ET.Element | None,
    nombre: str,
) -> ET.Element | None:
    if elemento is None:
        return None

    nombre_normalizado = nombre.lower()

    for hijo in elemento:
        if _nombre_local(hijo.tag) == nombre_normalizado:
            return hijo

    return None


def _buscar_hijos_directos(
    elemento: ET.Element | None,
    nombre: str,
) -> list[ET.Element]:
    if elemento is None:
        return []

    nombre_normalizado = nombre.lower()

    return [
        hijo
        for hijo in elemento
        if _nombre_local(hijo.tag) == nombre_normalizado
    ]


def _obtener_texto_directo(
    elemento: ET.Element | None,
    nombre: str,
    valor_predeterminado: str = "",
) -> str:
    hijo = _buscar_hijo_directo(
        elemento=elemento,
        nombre=nombre,
    )

    if hijo is None or hijo.text is None:
        return valor_predeterminado

    return hijo.text.strip()


def _obtener_entero(
    valor: str | None,
    valor_predeterminado: int = 0,
) -> int:
    if valor is None:
        return valor_predeterminado

    try:
        return int(float(valor.strip()))
    except (TypeError, ValueError):
        return valor_predeterminado


def _normalizar_texto(valor: Any) -> str:
    return str(valor or "").strip().casefold()


def _valor_booleano(valor: Any) -> bool:
    texto = _normalizar_texto(valor)

    return texto in {
        "1",
        "true",
        "yes",
        "y",
        "si",
        "sí",
        "enabled",
        "activo",
    }


def _limpiar_identificador(valor: str) -> str:
    limpio = valor.strip()

    if len(limpio) >= 2:
        pares = {'"': '"', "`": "`", "[": "]"}
        cierre = pares.get(limpio[0])

        if cierre and limpio[-1] == cierre:
            limpio = limpio[1:-1]

    return limpio.strip()


def _separar_tabla(valor: str) -> tuple[str, str]:
    partes = [
        _limpiar_identificador(parte)
        for parte in re.split(r"\s*\.\s*", valor)
        if parte.strip()
    ]

    if not partes:
        return "", ""

    return ".".join(partes[:-1]), partes[-1]


def _tabla_tecnica(esquema: str, nombre: str) -> dict[str, str]:
    return {
        "esquema": esquema,
        "nombre": nombre,
        "nombre_completo": f"{esquema}.{nombre}" if esquema else nombre,
    }


def _agregar_tabla_unica(
    destino: dict[str, dict[str, str]],
    esquema: str,
    nombre: str,
) -> None:
    nombre_limpio = _limpiar_identificador(nombre)
    esquema_limpio = ".".join(
        _limpiar_identificador(parte)
        for parte in esquema.split(".")
        if parte.strip()
    )

    if not nombre_limpio:
        return

    tabla = _tabla_tecnica(esquema_limpio, nombre_limpio)
    destino.setdefault(tabla["nombre_completo"].casefold(), tabla)


def _tablas_sql(
    sql: str,
    patrones: tuple[re.Pattern[str], ...] | list[re.Pattern[str]],
) -> list[dict[str, str]]:
    sin_comentarios = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sin_comentarios = re.sub(r"--[^\r\n]*", " ", sin_comentarios)
    tablas: dict[str, dict[str, str]] = {}

    for patron in patrones:
        for coincidencia in patron.finditer(sin_comentarios):
            esquema, nombre = _separar_tabla(coincidencia.group(1))
            _agregar_tabla_unica(tablas, esquema, nombre)

    return list(tablas.values())


def _extraer_detalle_transformacion(
    xml_element: ET.Element,
    elementos: list[dict[str, Any]],
    hops: list[dict[str, Any]],
) -> dict[str, Any]:
    """Extrae informacion tecnica serializable de una transformacion."""

    tablas_consultadas: dict[str, dict[str, str]] = {}
    tablas_destino: dict[str, dict[str, str]] = {}
    scripts_sql: list[dict[str, Any]] = []
    tipos_paso: dict[str, int] = {}
    usos_tablas: dict[tuple[str, str, str], dict[str, str]] = {}

    def registrar_uso(
        tabla: dict[str, str],
        paso: str,
        tipo_uso: str,
    ) -> None:
        nombre_completo = tabla.get("nombre_completo", "").strip()

        if not nombre_completo:
            return

        clave = (paso.casefold(), nombre_completo.casefold(), tipo_uso)
        usos_tablas.setdefault(
            clave,
            {
                **tabla,
                "paso": paso,
                "tipo_uso": tipo_uso,
            },
        )

    for indice, step in enumerate(
        _buscar_hijos_directos(xml_element, "step"),
        start=1,
    ):
        nombre_paso = _obtener_texto_directo(step, "name") or f"step_{indice}"
        tipo_paso = _obtener_texto_directo(step, "type", "UNKNOWN")
        tipo_normalizado = tipo_paso.casefold()
        tipos_paso[tipo_paso] = tipos_paso.get(tipo_paso, 0) + 1
        sql = _obtener_texto_directo(step, "sql")
        fuentes_script: list[dict[str, str]] = []
        destinos_script: list[dict[str, str]] = []

        if sql:
            fuentes_script = _tablas_sql(sql, [SOURCE_TABLE_PATTERN])
            destinos_script = _tablas_sql(sql, DESTINATION_TABLE_PATTERNS)

            for tabla in fuentes_script:
                _agregar_tabla_unica(
                    tablas_consultadas,
                    tabla["esquema"],
                    tabla["nombre"],
                )
                registrar_uso(tabla, nombre_paso, "CONSULTA")

            for tabla in destinos_script:
                _agregar_tabla_unica(
                    tablas_destino,
                    tabla["esquema"],
                    tabla["nombre"],
                )
                registrar_uso(tabla, nombre_paso, "DESTINO")

            scripts_sql.append(
                {
                    "paso": nombre_paso,
                    "tipo_paso": tipo_paso,
                    "sql": sql,
                    "tablas_consultadas": fuentes_script,
                    "tablas_destino": destinos_script,
                }
            )

        if tipo_normalizado in {"databaselookup", "dimensionlookup"}:
            esquema = _obtener_texto_directo(step, "schema")
            tabla = _obtener_texto_directo(step, "table")
            _agregar_tabla_unica(tablas_consultadas, esquema, tabla)
            tabla_lookup = _tabla_tecnica(
                _limpiar_identificador(esquema),
                _limpiar_identificador(tabla),
            )
            if tabla:
                registrar_uso(tabla_lookup, nombre_paso, "LOOKUP")

            if (
                tipo_normalizado == "dimensionlookup"
                and _valor_booleano(_obtener_texto_directo(step, "update"))
            ):
                _agregar_tabla_unica(tablas_destino, esquema, tabla)
                if tabla:
                    registrar_uso(tabla_lookup, nombre_paso, "DESTINO")

        if tipo_normalizado in {"tableoutput", "orabulkloader"}:
            esquema = _obtener_texto_directo(step, "schema")
            tabla = _obtener_texto_directo(step, "table")
            _agregar_tabla_unica(
                tablas_destino,
                esquema,
                tabla,
            )
            if tabla:
                registrar_uso(
                    _tabla_tecnica(
                        _limpiar_identificador(esquema),
                        _limpiar_identificador(tabla),
                    ),
                    nombre_paso,
                    "DESTINO",
                )

    parametros: list[str] = []
    info = _buscar_hijo_directo(xml_element, "info")
    contenedor_parametros = _buscar_hijo_directo(info, "parameters")

    for parametro in _buscar_hijos_directos(contenedor_parametros, "parameter"):
        nombre = _obtener_texto_directo(parametro, "name")
        if nombre and nombre not in parametros:
            parametros.append(nombre)

    xml_serializado = ET.tostring(xml_element, encoding="unicode")
    variables = sorted(
        {
            nombre.strip()
            for nombre in VARIABLE_PATTERN.findall(xml_serializado)
            if nombre.strip()
        },
        key=str.casefold,
    )

    return {
        "total_pasos": len(elementos),
        "total_hops": len(hops),
        "tipos_paso": [
            {"tipo": tipo, "cantidad": cantidad}
            for tipo, cantidad in sorted(
                tipos_paso.items(),
                key=lambda item: item[0].casefold(),
            )
        ],
        "tablas_consultadas": list(tablas_consultadas.values()),
        "tablas_destino": list(tablas_destino.values()),
        "tiene_sql": bool(scripts_sql),
        "scripts_sql": scripts_sql,
        "usos_tablas": list(usos_tablas.values()),
        "variables": variables,
        "parametros": sorted(parametros, key=str.casefold),
    }


def _normalizar_tipo_proceso(
    tipo: Any,
) -> str:
    texto = _normalizar_texto(tipo)

    if texto in {
        "job",
        "kjb",
    }:
        return "JOB"

    if texto in {
        "trans",
        "transformation",
        "transformacion",
        "transformación",
        "ktr",
    }:
        return "TRANSFORMATION"

    return str(tipo or "").strip().upper()


def _obtener_xml_element(
    proceso: dict[str, Any],
) -> ET.Element | None:
    """
    Localiza el elemento XML almacenado por el inventario legacy.
    """

    posibles_claves = (
        "xml_element",
        "elemento_xml",
        "xml",
        "element",
        "elemento",
    )

    for clave in posibles_claves:
        valor = proceso.get(clave)

        if isinstance(valor, ET.Element):
            return valor

    return None


def _crear_indice_inventario(
    inventario: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    indice: dict[str, dict[str, Any]] = {}

    colecciones = (
        inventario.get("jobs", {}),
        inventario.get("transformaciones", {}),
    )

    for coleccion in colecciones:
        if isinstance(coleccion, dict):
            elementos = coleccion.values()
        elif isinstance(coleccion, list):
            elementos = coleccion
        else:
            continue

        for elemento in elementos:
            if not isinstance(elemento, dict):
                continue

            identificador = str(
                elemento.get("id", "")
            ).strip()

            if identificador:
                indice[identificador] = elemento

    return indice


def _crear_indice_relaciones(
    relaciones: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """
    Indexa las relaciones por proceso origen y nombre
    de la entrada o step que realiza la llamada.
    """

    indice: dict[
        tuple[str, str],
        list[dict[str, Any]],
    ] = {}

    for relacion in relaciones:
        if not isinstance(relacion, dict):
            continue

        origen_id = str(
            relacion.get("origen_id", "")
        ).strip()

        entrada = _normalizar_texto(
            relacion.get("entrada")
        )

        if not origen_id or not entrada:
            continue

        clave = (
            origen_id,
            entrada,
        )

        indice.setdefault(
            clave,
            [],
        ).append(relacion)

    return indice


def _resolver_target(
    proceso_id: str,
    nombre_elemento: str,
    indice_relaciones: dict[
        tuple[str, str],
        list[dict[str, Any]],
    ],
) -> dict[str, Any] | None:
    relaciones = indice_relaciones.get(
        (
            proceso_id,
            _normalizar_texto(nombre_elemento),
        ),
        [],
    )

    if not relaciones:
        return None

    relaciones_resueltas = [
        relacion
        for relacion in relaciones
        if str(
            relacion.get("destino_id", "")
        ).strip()
    ]

    relacion = (
        relaciones_resueltas[0]
        if relaciones_resueltas
        else relaciones[0]
    )

    destino_id = str(
        relacion.get("destino_id", "")
    ).strip()

    estado = str(
        relacion.get("estado", "")
    ).strip()

    if not destino_id:
        return {
            "id": None,
            "nombre": relacion.get(
                "destino"
            ),
            "tipo": _normalizar_tipo_proceso(
                relacion.get("destino_tipo")
            ),
            "ruta": relacion.get(
                "destino_ruta"
            ),
            "estado": estado or "no_resuelto",
            "navegable": False,
        }

    return {
        "id": destino_id,
        "nombre": relacion.get("destino"),
        "tipo": _normalizar_tipo_proceso(
            relacion.get("destino_tipo")
        ),
        "ruta": relacion.get("destino_ruta"),
        "estado": estado or "resuelto",
        "navegable": True,
    }


def _extraer_posicion_job(
    entrada: ET.Element,
) -> tuple[int, int]:
    x = _obtener_entero(
        _obtener_texto_directo(
            entrada,
            "xloc",
        )
    )

    y = _obtener_entero(
        _obtener_texto_directo(
            entrada,
            "yloc",
        )
    )

    return x, y


def _extraer_posicion_transformacion(
    step: ET.Element,
) -> tuple[int, int]:
    gui = _buscar_hijo_directo(
        step,
        "GUI",
    )

    x = _obtener_entero(
        _obtener_texto_directo(
            gui,
            "xloc",
            _obtener_texto_directo(
                step,
                "xloc",
            ),
        )
    )

    y = _obtener_entero(
        _obtener_texto_directo(
            gui,
            "yloc",
            _obtener_texto_directo(
                step,
                "yloc",
            ),
        )
    )

    return x, y


def _extraer_hops(
    xml_element: ET.Element,
) -> list[dict[str, Any]]:
    contenedores: list[ET.Element] = []

    # Los Jobs guardan sus conexiones dentro de <hops>,
    # mientras que las transformaciones Pentaho suelen
    # guardarlas dentro de <order>.
    for nombre_contenedor in ("hops", "order"):
        contenedor = _buscar_hijo_directo(
            xml_element,
            nombre_contenedor,
        )

        if contenedor is not None:
            contenedores.append(contenedor)

    hops: list[dict[str, Any]] = []

    for contenedor_hops in contenedores:
        for hop in _buscar_hijos_directos(
            contenedor_hops,
            "hop",
        ):
            origen = _obtener_texto_directo(
                hop,
                "from",
            )

            destino = _obtener_texto_directo(
                hop,
                "to",
            )

            if not origen or not destino:
                continue

            habilitado_texto = (
                _obtener_texto_directo(
                    hop,
                    "enabled",
                    "Y",
                )
            )

            evaluacion = _obtener_texto_directo(
                hop,
                "evaluation",
            )

            incondicional = _obtener_texto_directo(
                hop,
                "unconditional",
            )

            hops.append(
                {
                    "origen": origen,
                    "destino": destino,
                    "habilitado": _valor_booleano(
                        habilitado_texto
                    ),
                    "evaluacion": evaluacion,
                    "incondicional": (
                        _valor_booleano(
                            incondicional
                        )
                    ),
                }
            )

    return hops


def _extraer_elementos_job(
    proceso_id: str,
    xml_element: ET.Element,
    indice_relaciones: dict[
        tuple[str, str],
        list[dict[str, Any]],
    ],
) -> list[dict[str, Any]]:
    contenedor_entries = _buscar_hijo_directo(
        xml_element,
        "entries",
    )

    elementos: list[dict[str, Any]] = []

    for indice, entrada in enumerate(
        _buscar_hijos_directos(
            contenedor_entries,
            "entry",
        ),
        start=1,
    ):
        nombre = _obtener_texto_directo(
            entrada,
            "name",
        )

        if not nombre:
            nombre = f"entry_{indice}"

        tipo = _obtener_texto_directo(
            entrada,
            "type",
            "UNKNOWN",
        )

        x, y = _extraer_posicion_job(
            entrada
        )

        target = _resolver_target(
            proceso_id=proceso_id,
            nombre_elemento=nombre,
            indice_relaciones=indice_relaciones,
        )

        elementos.append(
            {
                "id": (
                    f"{proceso_id}::entry::{indice}"
                ),
                "nombre": nombre,
                "tipo": tipo,
                "categoria": "ENTRY",
                "x": x,
                "y": y,
                "habilitado": True,
                "target": target,
                "navegable": bool(
                    target
                    and target.get("navegable")
                ),
            }
        )

    return elementos


def _extraer_elementos_transformacion(
    proceso_id: str,
    xml_element: ET.Element,
    indice_relaciones: dict[
        tuple[str, str],
        list[dict[str, Any]],
    ],
) -> list[dict[str, Any]]:
    elementos: list[dict[str, Any]] = []

    for indice, step in enumerate(
        _buscar_hijos_directos(
            xml_element,
            "step",
        ),
        start=1,
    ):
        nombre = _obtener_texto_directo(
            step,
            "name",
        )

        if not nombre:
            nombre = f"step_{indice}"

        tipo = _obtener_texto_directo(
            step,
            "type",
            "UNKNOWN",
        )

        x, y = _extraer_posicion_transformacion(
            step
        )

        target = _resolver_target(
            proceso_id=proceso_id,
            nombre_elemento=nombre,
            indice_relaciones=indice_relaciones,
        )

        elementos.append(
            {
                "id": (
                    f"{proceso_id}::step::{indice}"
                ),
                "nombre": nombre,
                "tipo": tipo,
                "categoria": "STEP",
                "x": x,
                "y": y,
                "habilitado": True,
                "target": target,
                "navegable": bool(
                    target
                    and target.get("navegable")
                ),
            }
        )

    return elementos


def _obtener_ids_alcanzables(
    resultado: dict[str, Any],
) -> set[str]:
    nodos = resultado.get(
        "nodos",
        {},
    )

    if isinstance(nodos, dict):
        return {
            str(identificador)
            for identificador in nodos.keys()
        }

    if isinstance(nodos, list):
        return {
            str(nodo.get("id"))
            for nodo in nodos
            if isinstance(nodo, dict)
            and nodo.get("id")
        }

    return set()


def construir_diagramas_proceso(
    inventario: dict[str, Any],
    resultado: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """
    Construye diagramas serializables de todos los Jobs y
    transformaciones alcanzables desde el Job principal.

    La salida contiene únicamente tipos primitivos para que
    pueda guardarse directamente en JSON.
    """

    indice_inventario = _crear_indice_inventario(
        inventario
    )

    relaciones_originales = resultado.get(
        "relaciones",
        [],
    )

    relaciones = (
        relaciones_originales
        if isinstance(
            relaciones_originales,
            list,
        )
        else []
    )

    indice_relaciones = _crear_indice_relaciones(
        relaciones
    )

    ids_alcanzables = _obtener_ids_alcanzables(
        resultado
    )

    diagramas: dict[
        str,
        dict[str, Any],
    ] = {}

    for proceso_id in ids_alcanzables:
        proceso_inventario = (
            indice_inventario.get(
                proceso_id
            )
        )

        if not proceso_inventario:
            continue

        tipo = _normalizar_tipo_proceso(
            proceso_inventario.get("tipo")
        )

        xml_element = _obtener_xml_element(
            proceso_inventario
        )

        if xml_element is None:
            continue

        if tipo == "JOB":
            elementos = _extraer_elementos_job(
                proceso_id=proceso_id,
                xml_element=xml_element,
                indice_relaciones=(
                    indice_relaciones
                ),
            )

        elif tipo == "TRANSFORMATION":
            elementos = (
                _extraer_elementos_transformacion(
                    proceso_id=proceso_id,
                    xml_element=xml_element,
                    indice_relaciones=(
                        indice_relaciones
                    ),
                )
            )

        else:
            continue

        hops = _extraer_hops(
            xml_element
        )

        detalle_tecnico = (
            _extraer_detalle_transformacion(
                xml_element=xml_element,
                elementos=elementos,
                hops=hops,
            )
            if tipo == "TRANSFORMATION"
            else None
        )

        posiciones_validas = [
            elemento
            for elemento in elementos
            if elemento["x"] != 0
            or elemento["y"] != 0
        ]

        max_x = max(
            (
                elemento["x"]
                for elemento in posiciones_validas
            ),
            default=0,
        )

        max_y = max(
            (
                elemento["y"]
                for elemento in posiciones_validas
            ),
            default=0,
        )

        diagramas[proceso_id] = {
            "id": proceso_id,
            "nombre": proceso_inventario.get(
                "nombre",
                "",
            ),
            "tipo": tipo,
            "ruta": proceso_inventario.get(
                "ruta",
                "",
            ),
            "elementos": elementos,
            "hops": hops,
            "detalle_tecnico": detalle_tecnico,
            "total_elementos": len(
                elementos
            ),
            "total_hops": len(hops),
            "ancho_recomendado": max(
                max_x + 220,
                900,
            ),
            "alto_recomendado": max(
                max_y + 180,
                600,
            ),
        }

    return diagramas
