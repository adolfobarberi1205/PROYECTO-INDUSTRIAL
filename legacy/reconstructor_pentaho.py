from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any
import xml.etree.ElementTree as ET


FALSE_VALUES = {"n", "no", "false", "0"}
ALL_FOLDERS_LABEL = "Todas las carpetas"
JOB_TYPE_HINTS = {
    "job",
    "jobexecutor",
    "job executor",
    "shelljob",
}
TRANS_TYPE_HINTS = {
    "trans",
    "transformation",
    "transjob",
    "trans executor",
    "transexecutor",
}
PATRON_REFERENCIA_VARIABLE = re.compile(r"\$\{([^}]+)\}|%%([^%]+)%%")
PATRON_VARIABLE_SHELL = re.compile(
    r"(?<!\\)\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?:[^}]*)\}|([A-Za-z_][A-Za-z0-9_]*))"
)
PATRON_ASIGNACION_SHELL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
EXTENSIONES_REPOSITORIO_CONOCIDAS = (".kjb", ".ktr", ".xml")


def normalizar_clave_kettle(nombre: str) -> str:
    """Normaliza ruido de archivo sin alterar mayúsculas ni minúsculas."""
    return (
        (nombre or "")
        .replace("\ufeff", "")
        .replace("\r", "")
        .replace("\n", "")
        .strip()
    )


def crear_indice_kettle_normalizado(
    propiedades: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    indice: dict[str, dict[str, Any]] = {}
    for clave, datos in propiedades.items():
        clave_normalizada = normalizar_clave_kettle(clave)
        indice[clave_normalizada] = {**datos, "nombre": clave_normalizada}
    return indice


def buscar_variable_en_kettle(
    nombre: str, indice_kettle_normalizado: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    return indice_kettle_normalizado.get(normalizar_clave_kettle(nombre))


def _enmascarar_valor_kettle(valor: Any) -> str:
    texto = "" if valor is None else str(valor)
    return "(vacío)" if not texto else f"[valor oculto; {len(texto)} caracteres]"


def _datos_kettle_seguros(datos: dict[str, Any]) -> dict[str, Any]:
    return {
        **{clave: valor for clave, valor in datos.items() if clave != "valor"},
        "valor_enmascarado": _enmascarar_valor_kettle(datos.get("valor", "")),
    }


def extraer_nombres_referenciados(valor: str) -> list[str]:
    """Devuelve referencias ${NOMBRE} y %%NOMBRE%% sin duplicados."""
    nombres: list[str] = []
    for coincidencia in PATRON_REFERENCIA_VARIABLE.finditer(valor or ""):
        nombre = normalizar_clave_kettle(
            coincidencia.group(1) or coincidencia.group(2) or ""
        )
        if nombre and nombre not in nombres:
            nombres.append(nombre)
    return nombres


def extraer_variables_shell(valor: str) -> list[str]:
    """Extrae referencias $NOMBRE y ${NOMBRE...} sin evaluar Shell."""
    nombres: list[str] = []
    for coincidencia in PATRON_VARIABLE_SHELL.finditer(valor or ""):
        nombre = coincidencia.group(1) or coincidencia.group(2) or ""
        if nombre and nombre not in nombres:
            nombres.append(nombre)
    return nombres


def crear_informacion_scripts_vacia(error: str = "") -> dict[str, Any]:
    return {
        "archivos": [],
        "asignaciones_parametros": [],
        "eventos_linaje": [],
        "resumen": {
            "archivos_cargados": 0,
            "invocaciones_kitchen": 0,
            "invocaciones_vinculadas_subgrafo": 0,
            "parametros_asignados": 0,
        },
        "errores": [error] if error else [],
    }


def _leer_texto_script(ruta: Path) -> tuple[str, str]:
    contenido = ruta.read_bytes()
    if contenido.startswith(b"\xef\xbb\xbf"):
        return contenido.decode("utf-8-sig"), "UTF-8 con BOM"
    try:
        return contenido.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        try:
            return contenido.decode("cp1252"), "CP1252"
        except UnicodeDecodeError:
            return contenido.decode("latin-1"), "Latin-1"


def _tokenizar_comandos_shell(contenido: str) -> list[tuple[int, list[str]]]:
    """Tokeniza órdenes sin expandir variables, sustituciones ni ejecutar código."""
    comandos: list[tuple[int, list[str]]] = []
    acumulada = ""
    linea_inicio = 1
    for numero, linea in enumerate(contenido.splitlines(), start=1):
        if not acumulada:
            linea_inicio = numero
        barras = len(linea) - len(linea.rstrip("\\"))
        continua = barras % 2 == 1
        fragmento = linea[:-1] if continua else linea
        acumulada += fragmento + (" " if continua else "")
        if continua:
            continue
        try:
            lexer = shlex.shlex(
                acumulada,
                posix=True,
                punctuation_chars=";&|",
            )
            lexer.whitespace_split = True
            lexer.commenters = "#"
            actual: list[str] = []
            for token in lexer:
                if token in {";", "&&", "||", "|", "&"}:
                    if actual:
                        comandos.append((linea_inicio, actual))
                        actual = []
                    continue
                actual.append(token)
            if actual:
                comandos.append((linea_inicio, actual))
        except ValueError:
            # Una línea con comillas incompletas no debe detener el resto del análisis.
            pass
        acumulada = ""
    return comandos


def _indice_kitchen_real(tokens: list[str]) -> int | None:
    """Acepta kitchen.sh como ejecutable real, nunca como argumento de echo."""
    if not tokens:
        return None
    ignorables = {"then", "do", "else", "{", "}", "command", "env", "nohup", "time"}
    wrappers_shell = {"sh", "bash"}
    indice_ejecutable = 0
    while indice_ejecutable < len(tokens):
        token = tokens[indice_ejecutable]
        if PATRON_ASIGNACION_SHELL.match(token) or token in ignorables:
            indice_ejecutable += 1
            continue
        break
    if indice_ejecutable >= len(tokens):
        return None
    ejecutable = Path(tokens[indice_ejecutable].replace("\\", "/")).name.lower()
    if ejecutable in {"echo", "printf"}:
        return None
    if ejecutable in wrappers_shell:
        indice_ejecutable += 1
        if indice_ejecutable >= len(tokens):
            return None
    token = tokens[indice_ejecutable].replace("\\", "/")
    return (
        indice_ejecutable
        if token.rsplit("/", 1)[-1].lower() == "kitchen.sh"
        else None
    )


def _extraer_opciones_kitchen(
    tokens: list[str], indice_kitchen: int
) -> dict[str, Any]:
    """Extrae solo opciones funcionales; user/pass se descartan por completo."""
    job = ""
    directorio = ""
    parametros: list[dict[str, str]] = []
    variables_credenciales: set[str] = set()
    indice = indice_kitchen + 1
    while indice < len(tokens):
        token = tokens[indice]
        coincidencia = re.match(
            r"^[-/]+(job|dir|user|pass)(?:=|:)(.*)$",
            token,
            flags=re.IGNORECASE,
        )
        clave = ""
        valor = ""
        if coincidencia:
            clave = coincidencia.group(1).lower()
            valor = coincidencia.group(2)
        elif token.lower() in {
            "-job", "/job", "-dir", "/dir", "-user", "/user", "-pass", "/pass"
        }:
            clave = token.lstrip("-/").lower()
            if indice + 1 < len(tokens):
                indice += 1
                valor = tokens[indice]
        if clave:
            if clave == "job":
                job = valor
            elif clave == "dir":
                directorio = valor
            else:
                variables_credenciales.update(extraer_variables_shell(valor))
            indice += 1
            continue

        coincidencia_param = re.match(
            r"^[-/]+param(?::|=)(.*)$", token, flags=re.IGNORECASE
        )
        carga = coincidencia_param.group(1) if coincidencia_param else ""
        if token.lower() in {"-param", "/param"} and indice + 1 < len(tokens):
            indice += 1
            carga = tokens[indice]
        if coincidencia_param or token.lower() in {"-param", "/param"}:
            if "=" in carga:
                nombre, valor_parametro = carga.split("=", 1)
                nombre = normalizar_clave_kettle(nombre)
                if nombre:
                    parametros.append(
                        {"nombre": nombre, "valor": valor_parametro}
                    )
        indice += 1
    return {
        "job": job,
        "dir": directorio,
        "parametros": parametros,
        "variables_credenciales": variables_credenciales,
    }


def _resolver_origen_parametro_script(
    valor: str, variables: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Resuelve procedencia Shell sin expandir ni calcular el valor."""
    expresion_original = valor
    referencias_iniciales = extraer_variables_shell(valor)
    intermedias: list[str] = []
    visitadas: set[str] = set()

    def resolver_expresion(expresion: str) -> tuple[str, list[str]]:
        referencias = extraer_variables_shell(expresion)
        if len(referencias) != 1:
            return expresion, referencias
        referencia = referencias[0]
        directa = expresion.strip() in {f"${referencia}", f"${{{referencia}}}"}
        asignacion = variables.get(referencia)
        if not directa or asignacion is None or referencia in visitadas:
            return expresion, referencias
        visitadas.add(referencia)
        intermedias.append(referencia)
        return resolver_expresion(asignacion.get("expresion", ""))

    expresion_resuelta, referencias = resolver_expresion(valor)
    if referencias:
        tipo = "VARIABLE_SHELL" if (
            len(referencias) == 1
            and expresion_resuelta.strip()
            in {f"${referencias[0]}", f"${{{referencias[0]}}}"}
        ) else "EXPRESION_SHELL"
    elif "$(" in expresion_resuelta or "`" in expresion_resuelta:
        tipo = "EXPRESION_SHELL"
    else:
        tipo = "LITERAL_SCRIPT"
    return {
        "origen_tipo": tipo,
        "origen": expresion_resuelta,
        "valor_configurado": expresion_original,
        "variables_shell": referencias,
        "variables_intermedias": intermedias,
    }


def _decodificar_escape_properties(valor: str) -> str:
    sustituciones = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}
    salida: list[str] = []
    indice = 0
    while indice < len(valor):
        if valor[indice] != "\\" or indice + 1 >= len(valor):
            salida.append(valor[indice])
            indice += 1
            continue
        siguiente = valor[indice + 1]
        if siguiente == "u":
            if indice + 5 >= len(valor):
                raise ValueError("Secuencia Unicode incompleta en Java Properties.")
            codigo = valor[indice + 2 : indice + 6]
            try:
                salida.append(chr(int(codigo, 16)))
                indice += 6
                continue
            except ValueError as error:
                raise ValueError(
                    "Secuencia Unicode inválida en Java Properties."
                ) from error
        salida.append(sustituciones.get(siguiente, siguiente))
        indice += 2
    return "".join(salida)


def _leer_texto_properties(ruta: Path) -> tuple[str, str]:
    """Decodifica un archivo Properties sin confundir UTF-16 con Latin-1."""
    contenido = ruta.read_bytes()
    if contenido.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return contenido.decode("utf-32"), "UTF-32 con BOM"
    if contenido.startswith((b"\xff\xfe", b"\xfe\xff")):
        orden = "LE" if contenido.startswith(b"\xff\xfe") else "BE"
        return contenido.decode("utf-16"), f"UTF-16 {orden} con BOM"
    if contenido.startswith(b"\xef\xbb\xbf"):
        return contenido.decode("utf-8-sig"), "UTF-8 con BOM"

    # Algunos kettle.properties se guardan desde editores de Windows como
    # UTF-16 sin BOM. UTF-8 acepta NUL, por lo que hay que detectarlo antes.
    if b"\x00" in contenido:
        pares = contenido[0::2]
        impares = contenido[1::2]
        proporcion_pares = pares.count(0) / max(1, len(pares))
        proporcion_impares = impares.count(0) / max(1, len(impares))
        if proporcion_impares > 0.3 and proporcion_pares < 0.1:
            return contenido.decode("utf-16-le"), "UTF-16 LE sin BOM"
        if proporcion_pares > 0.3 and proporcion_impares < 0.1:
            return contenido.decode("utf-16-be"), "UTF-16 BE sin BOM"
        raise ValueError(
            "El archivo de propiedades contiene bytes NUL y su codificación no pudo determinarse."
        )

    try:
        return contenido.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        try:
            return contenido.decode("cp1252"), "CP1252"
        except UnicodeDecodeError:
            # Properties.load(InputStream) usa ISO-8859-1.
            return contenido.decode("latin-1"), "Latin-1"


def _es_espacio_properties(caracter: str) -> bool:
    return caracter in " \t\f"


def _lineas_logicas_properties(contenido: str) -> list[tuple[int, str]]:
    """Une continuaciones siguiendo las reglas de java.util.Properties."""
    fisicas = re.split(r"\r\n|\n|\r", contenido)
    logicas: list[tuple[int, str]] = []
    indice = 0
    while indice < len(fisicas):
        numero = indice + 1
        linea = fisicas[indice]
        indice += 1
        texto_inicial = linea.lstrip(" \t\f")

        # En Java los comentarios son líneas naturales: una barra final en
        # un comentario no puede absorber la propiedad de la línea siguiente.
        if not texto_inicial or texto_inicial.startswith(("#", "!")):
            continue

        acumulada = linea
        while True:
            barras = len(acumulada) - len(acumulada.rstrip("\\"))
            if barras % 2 == 0:
                break
            acumulada = acumulada[:-1]
            if indice >= len(fisicas):
                break
            acumulada += fisicas[indice].lstrip(" \t\f")
            indice += 1
        logicas.append((numero, acumulada))
    return logicas


def cargar_kettle_properties(
    ruta: Path, diagnostico: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """Lee Java Properties reales conservando valor y línea de origen."""
    ruta = Path(ruta).expanduser().resolve()
    if not ruta.exists() or not ruta.is_file():
        raise FileNotFoundError(f"No existe el archivo de propiedades: {ruta}")
    contenido, codificacion = _leer_texto_properties(ruta)

    propiedades: dict[str, dict[str, Any]] = {}
    for numero, linea in _lineas_logicas_properties(contenido):
        texto = linea.lstrip(" \t\f")
        separador = -1
        escapado = False
        for indice, caracter in enumerate(texto):
            if escapado:
                escapado = False
                continue
            if caracter == "\\":
                escapado = True
                continue
            if caracter in "=:" or _es_espacio_properties(caracter):
                separador = indice
                break
        if separador < 0:
            clave_cruda, valor_crudo = texto, ""
        else:
            clave_cruda = texto[:separador]
            resto = texto[separador:]
            resto = resto.lstrip(" \t\f")
            if resto.startswith(("=", ":")):
                resto = resto[1:].lstrip(" \t\f")
            valor_crudo = resto
        try:
            clave = normalizar_clave_kettle(
                _decodificar_escape_properties(clave_cruda)
            )
            valor = _decodificar_escape_properties(valor_crudo)
        except ValueError as error:
            raise ValueError(
                f"No se pudo interpretar kettle.properties en la línea {numero}: {error}"
            ) from error
        propiedades[clave] = {
            "nombre": clave,
            "valor": valor,
            "linea": numero,
            "archivo": ruta.name,
            "ruta": str(ruta),
            "codificacion": codificacion,
        }
    if diagnostico is not None:
        lineas_fisicas = re.split(r"\r\n|\n|\r", contenido)
        cantidad_lineas = len(lineas_fisicas)
        if lineas_fisicas and lineas_fisicas[-1] == "":
            cantidad_lineas -= 1
        diagnostico.update(
            {
                "archivo": ruta.name,
                "ruta": str(ruta),
                "lineas_leidas": cantidad_lineas,
                "propiedades_detectadas": len(propiedades),
                "codificacion": codificacion,
                "primeras_claves": list(propiedades)[:5],
                "ultima_busqueda_fallida_repr": None,
            }
        )
    return propiedades


def comparar_variables_con_kettle(
    nombres_proceso: list[str],
    propiedades: dict[str, dict[str, Any]],
    resultado: dict[str, Any] | None = None,
    inventario: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nombres = list(
        dict.fromkeys(
            normalizar_clave_kettle(nombre)
            for nombre in nombres_proceso
            if normalizar_clave_kettle(nombre)
        )
    )
    indice_kettle = crear_indice_kettle_normalizado(propiedades)
    coincidencias = {
        nombre: _datos_kettle_seguros(datos)
        for nombre in nombres
        if (datos := buscar_variable_en_kettle(nombre, indice_kettle)) is not None
    }
    analisis_causal_disponible = resultado is not None and inventario is not None
    coincidencias_directas = (
        {} if analisis_causal_disponible else dict(coincidencias)
    )
    sin_coincidencia = [nombre for nombre in nombres if nombre not in coincidencias]
    derivadas: dict[str, dict[str, Any]] = {}
    origen_dinamico: list[str] = []
    origen_interno: list[str] = []
    origen_parametro: list[str] = []
    no_resueltas: list[str] = []
    creadas_renombradas: dict[str, list[dict[str, Any]]] = {}
    propiedades_usadas: set[str] = set()

    if analisis_causal_disponible:
        modelo = construir_modelo_linaje(resultado, inventario, propiedades)
        for nombre in nombres:
            resolucion = _resolver_dependencias_modelo(modelo, nombre, indice_kettle)
            fuentes = resolucion["fuentes_kettle"]
            propiedades_usadas.update(fuentes)
            if nombre in modelo["creaciones"]:
                creadas_renombradas[nombre] = modelo["creaciones"][nombre]
            if (
                nombre in fuentes
                and not resolucion["aristas_aguas_arriba"]
            ):
                datos_directos = buscar_variable_en_kettle(nombre, indice_kettle)
                if datos_directos is not None:
                    coincidencias_directas[nombre] = _datos_kettle_seguros(
                        datos_directos
                    )
                continue
            if fuentes:
                derivadas[nombre] = {
                    "origenes_kettle": fuentes,
                    "campos_intermedios": resolucion["campos_intermedios"],
                }
            elif resolucion["origenes_dinamicos"]:
                origen_dinamico.append(nombre)
            elif resolucion["origenes_internos"]:
                origen_interno.append(nombre)
            elif resolucion["origenes_parametros"]:
                origen_parametro.append(nombre)
            else:
                no_resueltas.append(nombre)
    else:
        no_resueltas = list(sin_coincidencia)
        propiedades_usadas.update(coincidencias)

    propiedades_kettle_usadas = {
        nombre: _datos_kettle_seguros(datos)
        for nombre in sorted(propiedades_usadas, key=lambda item: (item.lower(), item))
        if (datos := buscar_variable_en_kettle(nombre, indice_kettle)) is not None
    }
    claves_usadas_normalizadas = {
        normalizar_clave_kettle(nombre) for nombre in propiedades_kettle_usadas
    }
    total_ignoradas = len(set(indice_kettle) - claves_usadas_normalizadas)
    return {
        "variables_proceso": nombres,
        "coincidencias": coincidencias,
        "coincidencias_directas": coincidencias_directas,
        "sin_coincidencia": sin_coincidencia,
        "variables_derivadas_resueltas": derivadas,
        "variables_creadas_renombradas": creadas_renombradas,
        "variables_origen_dinamico": origen_dinamico,
        "variables_origen_interno": origen_interno,
        "variables_origen_parametro": origen_parametro,
        "variables_origen_no_resuelto": no_resueltas,
        "propiedades_kettle_usadas": propiedades_kettle_usadas,
        "busquedas_fallidas_repr": [repr(nombre) for nombre in no_resueltas[:5]],
        "total_propiedades": len(indice_kettle),
        "total_variables_proceso": len(nombres),
        "total_coincidencias": len(coincidencias),
        "total_coincidencias_directas": len(coincidencias_directas),
        "total_sin_coincidencia": len(sin_coincidencia),
        "total_variables_derivadas_resueltas": len(derivadas),
        "total_variables_creadas_renombradas": len(creadas_renombradas),
        "total_origen_dinamico": len(origen_dinamico),
        "total_origen_interno": len(origen_interno),
        "total_origen_parametro": len(origen_parametro),
        "total_origen_no_resuelto": len(no_resueltas),
        "total_propiedades_ignoradas": total_ignoradas,
        "total_ignoradas": total_ignoradas,
    }


def configurar_rutas_tcl_tk() -> None:
    base_dir = Path(sys.base_prefix)
    tcl_dir = base_dir / "tcl"
    tcl_library = tcl_dir / "tcl8.6"
    tk_library = tcl_dir / "tk8.6"

    if not os.environ.get("TCL_LIBRARY") and tcl_library.exists():
        os.environ["TCL_LIBRARY"] = tcl_library.as_posix()

    if not os.environ.get("TK_LIBRARY") and tk_library.exists():
        os.environ["TK_LIBRARY"] = tk_library.as_posix()


def obtener_texto_seguro(
    element: ET.Element | None,
    tags: str | list[str] | tuple[str, ...],
    default: str = "",
    *,
    deep: bool = False,
) -> str:
    if element is None:
        return default

    if isinstance(tags, str):
        tags = [tags]

    for tag in tags:
        candidato = element.find(f".//{tag}") if deep else element.find(tag)
        if candidato is not None and candidato.text:
            texto = candidato.text.strip()
            if texto:
                return texto
    return default


def obtener_textos_descendientes(element: ET.Element, nombres: set[str]) -> list[str]:
    valores: list[str] = []
    for sub in element.iter():
        if normalizar_nombre_etiqueta(sub.tag) in nombres and sub.text:
            texto = sub.text.strip()
            if texto:
                valores.append(texto)
    return valores


def obtener_texto_hijo_directo(element: ET.Element | None, tags: list[str] | tuple[str, ...]) -> str:
    if element is None:
        return ""
    tags_normalizados = {tag.lower() for tag in tags}
    for child in element:
        if normalizar_nombre_etiqueta(child.tag) in tags_normalizados and child.text:
            texto = child.text.strip()
            if texto:
                return texto
    return ""


def normalizar_nombre_etiqueta(tag: str) -> str:
    if "}" in tag:
        tag = tag.rsplit("}", 1)[-1]
    return tag.strip().lower()


def eliminar_extension_repositorio_conocida(nombre: str) -> str:
    texto = (nombre or "").strip()
    texto_comparacion = texto.casefold()
    for extension in EXTENSIONES_REPOSITORIO_CONOCIDAS:
        if texto_comparacion.endswith(extension):
            return texto[: -len(extension)]
    return texto


def obtener_nombre_base_repositorio(nombre: str) -> str:
    nombre_limpio = (nombre or "").strip().replace("\\", "/")
    if not nombre_limpio:
        return ""
    return eliminar_extension_repositorio_conocida(
        nombre_limpio.rstrip("/").rsplit("/", 1)[-1]
    ).strip()


def normalizar_nombre(nombre: str) -> str:
    return obtener_nombre_base_repositorio(nombre).casefold()


def normalizar_ruta(ruta: str) -> str:
    ruta = (ruta or "").strip().replace("\\", "/")
    if not ruta:
        return "/"
    if "://" in ruta:
        ruta = ruta.split("://", 1)[-1]
    if ruta.casefold().endswith(EXTENSIONES_REPOSITORIO_CONOCIDAS):
        ruta = ruta.rsplit("/", 1)[0] if "/" in ruta else ""
    partes: list[str] = []
    for parte in ruta.split("/"):
        parte = parte.strip()
        if not parte or parte == ".":
            continue
        if parte == "..":
            if partes:
                partes.pop()
            continue
        partes.append(parte)
    if not partes:
        return "/"
    return "/" + "/".join(partes)


def normalizar_ruta_comparacion(ruta: str) -> str:
    return normalizar_ruta(ruta).casefold()


def es_ruta_repositorio_absoluta(ruta: str) -> bool:
    ruta = (ruta or "").strip().replace("\\", "/")
    return ruta.startswith("/") or "://" in ruta or bool(re.match(r"^[A-Za-z]:/", ruta))


def unir_rutas_repositorio(ruta_base: str, ruta_referencia: str) -> str:
    referencia = (ruta_referencia or "").strip().replace("\\", "/")
    if not referencia:
        return normalizar_ruta(ruta_base)
    if es_ruta_repositorio_absoluta(referencia):
        return normalizar_ruta(referencia)
    base = normalizar_ruta(ruta_base)
    return normalizar_ruta(f"{base.rstrip('/')}/{referencia}")


def unir_ruta_y_nombre(ruta: str, nombre: str) -> str:
    ruta_normal = normalizar_ruta(ruta)
    nombre_limpio = (nombre or "").strip().replace("\\", "/")
    if not nombre_limpio:
        return ruta_normal
    if "/" in nombre_limpio:
        ruta_en_nombre, nombre_limpio = nombre_limpio.rsplit("/", 1)
        if es_ruta_repositorio_absoluta(ruta_en_nombre):
            ruta_normal = normalizar_ruta(ruta_en_nombre)
        else:
            ruta_normal = unir_rutas_repositorio(ruta_normal, ruta_en_nombre)
    nombre_limpio = eliminar_extension_repositorio_conocida(nombre_limpio).strip()
    if not nombre_limpio:
        return ruta_normal
    if ruta_normal == "/":
        return f"/{nombre_limpio}"
    return f"{ruta_normal}/{nombre_limpio}"


def es_falso(valor: str) -> bool:
    return (valor or "").strip().lower() in FALSE_VALUES


def convertir_entero(valor: str, default: int = 0) -> int:
    try:
        return int(float((valor or "").strip()))
    except ValueError:
        return default


def inferir_tipo_objeto(element: ET.Element) -> tuple[str, ET.Element] | None:
    tag = normalizar_nombre_etiqueta(element.tag)

    if tag == "job":
        return "JOB", element
    if tag in {"transformation", "trans"}:
        return "TRANSFORMATION", element

    for child in element:
        child_tag = normalizar_nombre_etiqueta(child.tag)
        if child_tag == "job":
            return "JOB", child
        if child_tag in {"transformation", "trans"}:
            return "TRANSFORMATION", child

    tiene_entries = element.find(".//entries/entry") is not None
    tiene_steps = element.find(".//step") is not None or element.find(".//order/step") is not None
    if tiene_entries and not tiene_steps:
        return "JOB", element
    if tiene_steps and not tiene_entries:
        return "TRANSFORMATION", element
    return None


def extraer_identificador_interno(element: ET.Element, tipo: str) -> str:
    tags = [
        "object_id",
        "job_object_id" if tipo == "JOB" else "trans_object_id",
        "objectId",
        "repository_object_id",
    ]

    identificador = obtener_texto_hijo_directo(element, tags)
    if identificador:
        return identificador

    info = element.find("info")
    identificador = obtener_texto_hijo_directo(info, tags)
    if identificador:
        return identificador

    return ""


def extraer_nombre_objeto_original(element: ET.Element, tipo: str) -> str:
    tags = [
        "name",
        "repositoryname",
        "jobname" if tipo == "JOB" else "transname",
        "filename",
        "file_name",
    ]
    texto = obtener_texto_seguro(element, tags, deep=False)
    if texto:
        return texto.strip()

    info = element.find("info")
    texto = obtener_texto_seguro(info, tags, deep=False)
    if texto:
        return texto.strip()

    for texto in obtener_textos_descendientes(element, {"name", "repositoryname"}):
        if texto:
            return texto.strip()
    return ""


def extraer_nombre_objeto(element: ET.Element, tipo: str) -> str:
    nombre_original = extraer_nombre_objeto_original(element, tipo)
    if "/" in nombre_original or "\\" in nombre_original:
        return obtener_nombre_base_repositorio(nombre_original)
    return nombre_original


def extraer_ruta_objeto_original(element: ET.Element) -> str:
    tags = [
        "directory",
        "path",
        "folder",
        "repository_directory",
        "directory_path",
    ]
    ruta = obtener_texto_seguro(element, tags, deep=False)
    if ruta:
        return ruta.strip()

    info = element.find("info")
    ruta = obtener_texto_seguro(info, tags, deep=False)
    if ruta:
        return ruta.strip()

    for texto in obtener_textos_descendientes(element, set(tags)):
        if texto:
            return texto.strip()

    filename = obtener_texto_seguro(element, ["filename", "file_name"], deep=False)
    if filename:
        filename = filename.strip().replace("\\", "/")
        if "/" in filename:
            return filename.rsplit("/", 1)[0] or "/"

    return "/"


def extraer_ruta_objeto(element: ET.Element) -> str:
    return normalizar_ruta(extraer_ruta_objeto_original(element))


def construir_objeto_repositorio(
    element: ET.Element,
    tipo: str,
    indice: int,
) -> dict[str, Any] | None:
    nombre = extraer_nombre_objeto(element, tipo)
    if not nombre:
        return None

    nombre_original = extraer_nombre_objeto_original(element, tipo) or nombre.strip()
    ruta_original = extraer_ruta_objeto_original(element)
    ruta = normalizar_ruta(ruta_original)
    identificador_interno = extraer_identificador_interno(element, tipo)
    uid = identificador_interno.strip() or f"{tipo}|{ruta}|{normalizar_nombre(nombre)}|{indice}"
    ruta_completa = unir_ruta_y_nombre(ruta, nombre)

    objeto = {
        "id": uid,
        "nombre": nombre_original,
        "nombre_original": nombre_original,
        "nombre_normalizado": normalizar_nombre(nombre_original),
        "tipo": tipo,
        "ruta": ruta,
        "ruta_original": ruta_original,
        "ruta_normalizada": normalizar_ruta_comparacion(ruta),
        "ruta_completa": ruta_completa,
        "ruta_completa_normalizada": ruta_completa.casefold(),
        "identificador_interno": identificador_interno.strip(),
        "xml_element": element,
    }

    if tipo == "JOB":
        entradas = extraer_entradas_job(element)
        for entrada in entradas:
            entrada["proceso_padre_id"] = uid
            entrada["proceso_padre_nombre"] = nombre_original
            entrada["proceso_padre_ruta"] = ruta
        objeto["entradas"] = entradas
        objeto["referencias_jobs"] = sum(1 for entrada in entradas if entrada["tipo_esperado"] == "JOB")
        objeto["referencias_transformaciones"] = sum(
            1 for entrada in entradas if entrada["tipo_esperado"] == "TRANSFORMATION"
        )
    return objeto


def insertar_objeto_sin_sobrescribir(
    destino: dict[str, dict[str, Any]],
    objeto: dict[str, Any],
    indice: int,
) -> None:
    uid_original = objeto["id"]
    uid = uid_original
    sufijo = indice
    while uid in destino:
        existente = destino[uid]
        mismo_objeto = (
            existente.get("nombre") == objeto.get("nombre")
            and existente.get("ruta") == objeto.get("ruta")
            and existente.get("tipo") == objeto.get("tipo")
        )
        uid = f"{uid_original}|duplicado_{sufijo}"
        sufijo += 1
        if mismo_objeto:
            break
    objeto["id"] = uid
    for entrada in objeto.get("entradas", []):
        entrada["proceso_padre_id"] = uid
    destino[uid] = objeto


def asegurar_ids_catalogo_globalmente_unicos(
    jobs: dict[str, dict[str, Any]],
    transformaciones: dict[str, dict[str, Any]],
) -> None:
    ids_usados = set(jobs)
    transformaciones_reindexadas: dict[str, dict[str, Any]] = {}
    for objeto_id, transformacion in transformaciones.items():
        id_global = objeto_id
        if id_global in ids_usados:
            base = f"TRANSFORMATION|{objeto_id}"
            id_global = base
            sufijo = 2
            while id_global in ids_usados or id_global in transformaciones_reindexadas:
                id_global = f"{base}|{sufijo}"
                sufijo += 1
            transformacion["id"] = id_global
        transformaciones_reindexadas[id_global] = transformacion
        ids_usados.add(id_global)
    transformaciones.clear()
    transformaciones.update(transformaciones_reindexadas)


def construir_indices_objetos(
    objetos: dict[str, dict[str, Any]],
) -> dict[str, dict[Any, list[str]]]:
    por_id: dict[str, list[str]] = {}
    por_ruta_nombre: dict[tuple[str, str], list[str]] = {}
    por_nombre: dict[str, list[str]] = {}

    for objeto_id, objeto in objetos.items():
        nombre_normalizado = objeto.get("nombre_normalizado") or normalizar_nombre(
            objeto.get("nombre", "")
        )
        ruta_normalizada = objeto.get("ruta_normalizada") or normalizar_ruta_comparacion(
            objeto.get("ruta", "")
        )
        objeto["nombre_normalizado"] = nombre_normalizado
        objeto["ruta_normalizada"] = ruta_normalizada
        objeto.setdefault("nombre_original", objeto.get("nombre", ""))
        objeto.setdefault("ruta_original", objeto.get("ruta", "/"))

        identificador = (objeto.get("identificador_interno") or "").strip().casefold()
        if identificador:
            por_id.setdefault(identificador, []).append(objeto_id)
        if nombre_normalizado:
            por_nombre.setdefault(nombre_normalizado, []).append(objeto_id)
            por_ruta_nombre.setdefault(
                (ruta_normalizada, nombre_normalizado), []
            ).append(objeto_id)

    return {
        "por_id": por_id,
        "por_ruta_nombre": por_ruta_nombre,
        "por_nombre": por_nombre,
    }


def construir_indices_globales(
    jobs: dict[str, dict[str, Any]],
    transformaciones: dict[str, dict[str, Any]],
) -> dict[str, dict[str, dict[Any, list[str]]]]:
    return {
        "jobs": construir_indices_objetos(jobs),
        "transformaciones": construir_indices_objetos(transformaciones),
    }


def detectar_carpetas(raiz: ET.Element) -> list[str]:
    carpetas: set[str] = {"/"}
    for elemento in raiz.iter():
        for tag in ("directory", "path", "folder", "repository_directory", "directory_path"):
            texto = obtener_texto_seguro(elemento, tag)
            if texto:
                carpetas.add(normalizar_ruta(texto))
    return sorted(carpetas)


def extraer_entradas_job(job_element: ET.Element) -> list[dict[str, Any]]:
    entradas: list[dict[str, Any]] = []
    for indice, entry in enumerate(job_element.findall(".//entries/entry"), start=1):
        tipo = obtener_texto_seguro(entry, "type")
        nombre = obtener_texto_seguro(entry, "name") or f"entry_{indice}"
        tipo_esperado = detectar_tipo_referencia_entrada(entry, tipo)
        referencia = construir_referencia_entrada(entry, tipo_esperado)
        entradas.append(
            {
                "indice": indice,
                "nombre": nombre,
                "tipo_entrada": tipo,
                "tipo_esperado": tipo_esperado,
                "habilitada": not es_falso(obtener_texto_seguro(entry, "enabled", "Y")),
                "referencia_original": referencia["texto_original"],
                "referencia_nombre": referencia["nombre"],
                "referencia_nombre_normalizado": referencia["nombre_normalizado"],
                "referencia_ruta": referencia["ruta"],
                "referencia_ruta_original": referencia["ruta_original"],
                "referencia_ruta_normalizada": referencia["ruta_normalizada"],
                "referencia_objeto_id": referencia["objeto_id"],
                "referencia_variables": referencia["variables"],
                "detalle": referencia["detalle"],
                "posicion_x": convertir_entero(obtener_texto_seguro(entry, "xloc")),
                "posicion_y": convertir_entero(obtener_texto_seguro(entry, "yloc")),
                "draw": obtener_texto_seguro(entry, "draw"),
                "xml_element": entry,
            }
        )
    return entradas


def extraer_hops_job(job_element: ET.Element) -> list[dict[str, str]]:
    hops: list[dict[str, str]] = []
    for hop in job_element.findall("./hops/hop"):
        hops.append(
            {
                "origen": obtener_texto_seguro(hop, "from"),
                "destino": obtener_texto_seguro(hop, "to"),
                "habilitado": obtener_texto_seguro(hop, "enabled", "Y"),
                "evaluacion": obtener_texto_seguro(hop, "evaluation"),
                "incondicional": obtener_texto_seguro(hop, "unconditional"),
            }
        )
    return hops


def extraer_pasos_transformacion(trans_element: ET.Element) -> list[dict[str, Any]]:
    pasos: list[dict[str, Any]] = []
    for indice, step in enumerate(trans_element.findall("./step"), start=1):
        pasos.append(
            {
                "indice": indice,
                "nombre": obtener_texto_seguro(step, "name") or f"step_{indice}",
                "tipo_entrada": obtener_texto_seguro(step, "type"),
                "posicion_x": convertir_entero(obtener_texto_seguro(step, "xloc")),
                "posicion_y": convertir_entero(obtener_texto_seguro(step, "yloc")),
                "draw": obtener_texto_seguro(step, "draw"),
            }
        )
    return pasos


def extraer_hops_transformacion(trans_element: ET.Element) -> list[dict[str, str]]:
    hops: list[dict[str, str]] = []
    for hop in trans_element.findall("./order/hop"):
        hops.append(
            {
                "origen": obtener_texto_seguro(hop, "from"),
                "destino": obtener_texto_seguro(hop, "to"),
                "habilitado": obtener_texto_seguro(hop, "enabled", "Y"),
                "evaluacion": "",
                "incondicional": "",
            }
        )
    return hops


def detectar_tipo_referencia_entrada(entry: ET.Element, tipo: str) -> str:
    tipo_normalizado = normalizar_nombre(tipo)
    if tipo_normalizado in JOB_TYPE_HINTS:
        if obtener_texto_seguro(entry, ["jobname", "job_object_id", "filename", "directory"], deep=True):
            return "JOB"
    if tipo_normalizado in TRANS_TYPE_HINTS:
        if obtener_texto_seguro(entry, ["transname", "trans_object_id", "filename", "directory"], deep=True):
            return "TRANSFORMATION"

    if obtener_texto_seguro(entry, ["jobname", "job_object_id"], deep=True):
        return "JOB"
    if obtener_texto_seguro(entry, ["transname", "trans_object_id"], deep=True):
        return "TRANSFORMATION"

    especificacion = normalizar_nombre(obtener_texto_seguro(entry, "specification_method", deep=True))
    if "job" in especificacion:
        return "JOB"
    if "trans" in especificacion:
        return "TRANSFORMATION"

    tipo_texto = (tipo or "").strip().lower()
    if "job" in tipo_texto:
        return "JOB"
    if "trans" in tipo_texto:
        return "TRANSFORMATION"
    return "UNKNOWN"


def construir_referencia_entrada(entry: ET.Element, tipo_esperado: str) -> dict[str, Any]:
    tags_nombre = ["jobname", "repositoryname"] if tipo_esperado == "JOB" else ["transname", "repositoryname"]
    tags_id = ["job_object_id"] if tipo_esperado == "JOB" else ["trans_object_id"]

    nombre_repositorio = obtener_texto_seguro(entry, tags_nombre, deep=False)
    if not nombre_repositorio:
        nombre_repositorio = obtener_texto_seguro(entry, tags_nombre, deep=True)
    directory_repositorio = obtener_texto_seguro(
        entry, ["directory", "path", "folder"], deep=True
    )
    filename = obtener_texto_seguro(entry, ["filename", "file_name"], deep=True)
    objeto_id_original = obtener_texto_seguro(entry, tags_id, deep=True)
    metodo_original = obtener_texto_seguro(entry, "specification_method", deep=True)
    metodo = metodo_original.strip().casefold()
    usa_filename = metodo in {"filename", "file_name", "file"}
    usa_nombre_repositorio = metodo in {
        "rep_name",
        "repository_name",
        "repository_by_name",
    }
    usa_id_repositorio = metodo in {
        "rep_ref",
        "repository_reference",
        "repository_by_reference",
        "object_id",
    }

    nombre = nombre_repositorio
    directory = directory_repositorio
    objeto_id = objeto_id_original
    filename_resolucion = filename
    if usa_filename:
        nombre = ""
        directory = ""
        objeto_id = ""
    elif usa_nombre_repositorio:
        objeto_id = ""
        filename_resolucion = ""
    elif usa_id_repositorio:
        nombre = ""
        directory = ""
        filename_resolucion = ""

    if filename_resolucion:
        filename_normalizado = filename_resolucion.strip().replace("\\", "/")
        nombre_archivo = filename_normalizado.rsplit("/", 1)[-1]
        if not nombre:
            nombre = obtener_nombre_base_repositorio(nombre_archivo)
        if not directory and "/" in filename_normalizado:
            directory = filename_normalizado.rsplit("/", 1)[0] or "/"

    if (
        not nombre
        and not usa_id_repositorio
        and tipo_esperado in {"JOB", "TRANSFORMATION"}
    ):
        nombre = obtener_texto_seguro(entry, "name")

    ruta = normalizar_ruta(directory) if directory else ""
    variables = extraer_nombres_referenciados(
        " | ".join(valor for valor in (nombre, directory, objeto_id) if valor)
    )
    texto_original = " | ".join(
        valor
        for valor in [
            obtener_texto_seguro(entry, "type"),
            nombre_repositorio,
            filename,
            directory_repositorio,
            objeto_id_original,
            metodo_original,
        ]
        if valor
    )

    detalle = {
        "filename": filename,
        "directory": directory_repositorio,
        "specification_method": metodo_original,
        "criterio_activo": (
            "filename"
            if usa_filename
            else "repository_name"
            if usa_nombre_repositorio
            else "repository_id"
            if usa_id_repositorio
            else "heuristico"
        ),
    }

    return {
        "nombre": nombre.strip(),
        "nombre_normalizado": normalizar_nombre(nombre),
        "ruta": ruta,
        "ruta_original": directory.strip(),
        "ruta_normalizada": normalizar_ruta_comparacion(ruta) if ruta else "",
        "objeto_id": objeto_id.strip(),
        "variables": variables,
        "texto_original": texto_original or obtener_texto_seguro(entry, "name") or "referencia_desconocida",
        "detalle": json.dumps(detalle, ensure_ascii=False),
    }


def extraer_jobs(raiz: ET.Element) -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = {}
    indice = 0
    vistos: set[int] = set()

    candidatos = raiz.findall("./jobs/job")
    if not candidatos:
        candidatos = [elemento for elemento in raiz.iter() if normalizar_nombre_etiqueta(elemento.tag) == "job"]

    for elemento in candidatos:
        if id(elemento) in vistos:
            continue
        vistos.add(id(elemento))
        indice += 1
        candidato = construir_objeto_repositorio(elemento, "JOB", indice)
        if not candidato:
            continue
        insertar_objeto_sin_sobrescribir(jobs, candidato, indice)
    return jobs


def extraer_transformaciones(raiz: ET.Element) -> dict[str, dict[str, Any]]:
    transformaciones: dict[str, dict[str, Any]] = {}
    indice = 0
    vistos: set[int] = set()

    candidatos = raiz.findall("./transformations/transformation")
    candidatos.extend(raiz.findall("./transformations/trans"))
    if not candidatos:
        candidatos = [
            elemento
            for elemento in raiz.iter()
            if normalizar_nombre_etiqueta(elemento.tag) in {"transformation", "trans"}
        ]

    for elemento in candidatos:
        if id(elemento) in vistos:
            continue
        vistos.add(id(elemento))
        indice += 1
        candidato = construir_objeto_repositorio(elemento, "TRANSFORMATION", indice)
        if not candidato:
            continue
        insertar_objeto_sin_sobrescribir(transformaciones, candidato, indice)
    return transformaciones


def extraer_inventario_repositorio(raiz: ET.Element) -> dict[str, Any]:
    jobs = extraer_jobs(raiz)
    transformaciones = extraer_transformaciones(raiz)
    asegurar_ids_catalogo_globalmente_unicos(jobs, transformaciones)
    indices_globales = construir_indices_globales(jobs, transformaciones)
    carpetas = sorted(
        {
            "/",
            *(job["ruta"] for job in jobs.values()),
            *(trans["ruta"] for trans in transformaciones.values()),
        }
    )

    return {
        "jobs": jobs,
        "transformaciones": transformaciones,
        "carpetas": carpetas,
        "indices_globales": indices_globales,
    }


def cargar_xml_repositorio(ruta_xml: str | Path) -> tuple[ET.Element, dict[str, Any]]:
    ruta = Path(ruta_xml).expanduser().resolve()
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo XML: {ruta}")
    if not ruta.is_file():
        raise ValueError(f"La ruta indicada no es un archivo: {ruta}")

    try:
        arbol = ET.parse(ruta)
    except ET.ParseError as error:
        raise ValueError(f"El XML no es válido: {error}") from error

    raiz = arbol.getroot()
    inventario = extraer_inventario_repositorio(raiz)
    return raiz, inventario


def filtrar_jobs_catalogo_por_carpeta(
    inventario: dict[str, Any], carpeta: str
) -> list[dict[str, Any]]:
    carpeta = (carpeta or "").strip()
    if carpeta == ALL_FOLDERS_LABEL:
        jobs = list(inventario.get("jobs", {}).values())
    else:
        ruta_seleccionada = normalizar_ruta_comparacion(carpeta or "/")
        jobs = [
            job
            for job in inventario.get("jobs", {}).values()
            if normalizar_ruta_comparacion(job.get("ruta", "/")) == ruta_seleccionada
        ]
    return sorted(
        jobs,
        key=lambda item: (
            normalizar_ruta_comparacion(item.get("ruta", "/")),
            item.get("nombre", "").casefold(),
            item.get("id", ""),
        ),
    )


def buscar_objeto_referenciado(
    referencia: dict[str, Any],
    objetos: dict[str, dict[str, Any]],
    indices: dict[str, dict[Any, list[str]]] | None = None,
) -> dict[str, Any]:
    indices = indices or construir_indices_objetos(objetos)

    def objetos_desde_ids(ids: list[str]) -> list[dict[str, Any]]:
        return [objetos[objeto_id] for objeto_id in ids if objeto_id in objetos]

    def sin_duplicados(candidatos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        vistos: set[str] = set()
        resultado: list[dict[str, Any]] = []
        for candidato in candidatos:
            if candidato["id"] in vistos:
                continue
            vistos.add(candidato["id"])
            resultado.append(candidato)
        return resultado

    def resumir(candidato: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": candidato.get("id", ""),
            "identificador_interno": candidato.get("identificador_interno", ""),
            "nombre": candidato.get("nombre", ""),
            "nombre_normalizado": candidato.get("nombre_normalizado", ""),
            "tipo": candidato.get("tipo", ""),
            "ruta": candidato.get("ruta", ""),
            "ruta_normalizada": candidato.get("ruta_normalizada", ""),
        }

    nombre_solicitado = (referencia.get("nombre") or "").strip()
    ruta_solicitada = (
        referencia.get("ruta_original")
        if referencia.get("ruta_original") not in (None, "")
        else referencia.get("ruta", "")
    )
    ruta_solicitada = (ruta_solicitada or "").strip()
    objeto_id = (referencia.get("objeto_id") or "").strip()
    nombre_normalizado = referencia.get("nombre_normalizado") or normalizar_nombre(
        nombre_solicitado
    )
    ruta_normalizada = (
        normalizar_ruta_comparacion(ruta_solicitada) if ruta_solicitada else ""
    )
    variables = list(referencia.get("variables") or [])
    if not variables:
        variables = extraer_nombres_referenciados(
            " | ".join(
                valor
                for valor in (nombre_solicitado, ruta_solicitada, objeto_id)
                if valor
            )
        )

    ids_por_id = indices["por_id"].get(objeto_id.casefold(), []) if objeto_id else []
    por_id = objetos_desde_ids(ids_por_id)
    ids_por_nombre = (
        indices["por_nombre"].get(nombre_normalizado, []) if nombre_normalizado else []
    )
    por_nombre = objetos_desde_ids(ids_por_nombre)

    claves_ruta: list[tuple[tuple[str, str], str]] = []
    claves_vistas: set[tuple[str, str]] = set()

    def agregar_clave_ruta(ruta: str, criterio: str) -> None:
        if not ruta or not nombre_normalizado:
            return
        clave = (normalizar_ruta_comparacion(ruta), nombre_normalizado)
        if clave in claves_vistas:
            return
        claves_vistas.add(clave)
        claves_ruta.append((clave, criterio))

    nombre_con_barras = nombre_solicitado.replace("\\", "/")
    if "/" in nombre_con_barras:
        ruta_en_nombre = nombre_con_barras.rsplit("/", 1)[0]
        if es_ruta_repositorio_absoluta(ruta_en_nombre):
            agregar_clave_ruta(ruta_en_nombre, "RUTA_COMPLETA_NOMBRE")
        else:
            base_referencia = ruta_solicitada or referencia.get("ruta", "") or "/"
            agregar_clave_ruta(
                unir_rutas_repositorio(base_referencia, ruta_en_nombre),
                "RUTA_COMPLETA_NOMBRE",
            )

    ruta_padre = (referencia.get("ruta_padre") or "").strip()
    if ruta_solicitada:
        if ruta_padre and not es_ruta_repositorio_absoluta(ruta_solicitada):
            agregar_clave_ruta(
                unir_rutas_repositorio(ruta_padre, ruta_solicitada),
                "RUTA_RELATIVA_PADRE_NOMBRE",
            )
        agregar_clave_ruta(ruta_solicitada, "RUTA_REFERENCIA_NOMBRE")
    elif referencia.get("ruta"):
        agregar_clave_ruta(referencia["ruta"], "RUTA_REFERENCIA_NOMBRE")

    por_ruta: list[dict[str, Any]] = []
    coincidencias_ruta: list[tuple[list[dict[str, Any]], str]] = []
    for clave, criterio in claves_ruta:
        encontrados = objetos_desde_ids(indices["por_ruta_nombre"].get(clave, []))
        por_ruta.extend(encontrados)
        coincidencias_ruta.append((encontrados, criterio))
    por_ruta = sin_duplicados(por_ruta)

    diagnostico = {
        "nombre_solicitado": nombre_solicitado,
        "ruta_solicitada": ruta_solicitada,
        "nombre_normalizado": nombre_normalizado,
        "ruta_normalizada": ruta_normalizada,
        "identificador_repositorio": objeto_id,
        "variables_detectadas": variables,
        "coincidencias_por_id": [resumir(item) for item in por_id],
        "coincidencias_por_ruta_nombre": [resumir(item) for item in por_ruta],
        "coincidencias_por_nombre": [resumir(item) for item in por_nombre],
        "candidatos_ambiguos": [],
        "rutas_candidatas": [],
        "criterio": "",
        "resultado_final": "",
        "motivo_codigo": "",
    }

    def finalizar(
        estado: str,
        motivo_codigo: str,
        motivo: str,
        criterio: str,
        coincidencias: list[dict[str, Any]],
        candidatos_ambiguos: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        candidatos = candidatos_ambiguos or []
        diagnostico["criterio"] = criterio
        diagnostico["resultado_final"] = estado
        diagnostico["motivo_codigo"] = motivo_codigo
        diagnostico["candidatos_ambiguos"] = [resumir(item) for item in candidatos]
        diagnostico["rutas_candidatas"] = sorted(
            {item.get("ruta", "") for item in candidatos if item.get("ruta")}
        )
        return {
            "estado": estado,
            "motivo": motivo,
            "motivo_codigo": motivo_codigo,
            "criterio": criterio,
            "coincidencias": coincidencias,
            "candidatos_ambiguos": diagnostico["candidatos_ambiguos"],
            "diagnostico": diagnostico,
        }

    if len(por_id) == 1:
        return finalizar("resuelta", "", "", "IDENTIFICADOR_REPOSITORIO", por_id)

    if variables:
        return finalizar(
            "referencia_dinamica",
            "REFERENCIA_DINAMICA",
            "La referencia usa una variable y no se intentó inventar su valor.",
            "VARIABLE_EN_NOMBRE_O_RUTA",
            [],
        )

    ids_coincidentes = {item["id"] for item in por_id}
    for encontrados, criterio in coincidencias_ruta:
        if not encontrados:
            continue
        refinados = (
            [item for item in encontrados if item["id"] in ids_coincidentes]
            if len(por_id) > 1
            else encontrados
        )
        if len(refinados) == 1:
            criterio_final = (
                f"IDENTIFICADOR_NO_UNICO+{criterio}" if len(por_id) > 1 else criterio
            )
            return finalizar("resuelta", "", "", criterio_final, refinados)
        if len(refinados) > 1:
            return finalizar(
                "ambigua",
                "NOMBRE_AMBIGUO",
                "La ruta y el nombre coinciden con varios procesos.",
                criterio,
                refinados,
                refinados,
            )
        if len(encontrados) == 1:
            return finalizar("resuelta", "", "", criterio, encontrados)
        return finalizar(
            "ambigua",
            "NOMBRE_AMBIGUO",
            "La ruta y el nombre coinciden con varios procesos.",
            criterio,
            encontrados,
            encontrados,
        )

    if len(por_id) > 1 and nombre_normalizado:
        refinados_nombre = [item for item in por_nombre if item["id"] in ids_coincidentes]
        if len(refinados_nombre) == 1:
            return finalizar(
                "resuelta", "", "", "IDENTIFICADOR_NO_UNICO+NOMBRE", refinados_nombre
            )
        if len(refinados_nombre) > 1:
            return finalizar(
                "ambigua",
                "NOMBRE_AMBIGUO",
                "El identificador y el nombre coinciden con varios procesos.",
                "IDENTIFICADOR_NO_UNICO+NOMBRE",
                refinados_nombre,
                refinados_nombre,
            )

    if len(por_nombre) == 1:
        return finalizar("resuelta", "", "", "NOMBRE_UNICO_GLOBAL", por_nombre)
    if len(por_nombre) > 1:
        return finalizar(
            "ambigua",
            "NOMBRE_AMBIGUO",
            "El nombre coincide con varios procesos y falta una ruta o ID confiable.",
            "NOMBRE_GLOBAL",
            por_nombre,
            por_nombre,
        )
    if len(por_id) > 1:
        return finalizar(
            "ambigua",
            "IDENTIFICADOR_AMBIGUO",
            "El identificador coincide con varios procesos y no hay datos para distinguirlos.",
            "IDENTIFICADOR_REPOSITORIO",
            por_id,
            por_id,
        )
    if objeto_id and not nombre_normalizado and not ruta_solicitada:
        return finalizar(
            "referencia_no_resuelta",
            "IDENTIFICADOR_NO_ENCONTRADO",
            "El identificador de repositorio no existe en el XML cargado.",
            "IDENTIFICADOR_REPOSITORIO",
            [],
        )
    if not nombre_normalizado:
        return finalizar(
            "referencia_no_resuelta",
            "DATOS_INSUFICIENTES",
            "La referencia no contiene un nombre utilizable.",
            "SIN_CRITERIO",
            [],
        )
    if ruta_solicitada:
        return finalizar(
            "referencia_no_resuelta",
            "RUTA_NO_COINCIDE",
            "La ruta y el nombre no coinciden con ningún proceso del XML cargado.",
            "RUTA_REFERENCIA_NOMBRE",
            [],
        )
    return finalizar(
        "referencia_no_resuelta",
        "NO_EXISTE_EN_XML",
        "No existe un proceso con ese nombre en el XML cargado.",
        "NOMBRE_GLOBAL",
        [],
    )


def buscar_job_referenciado(referencia: dict[str, Any], inventario: dict[str, Any]) -> dict[str, Any]:
    indices_globales = inventario.setdefault("indices_globales", {})
    indices = indices_globales.get("jobs")
    if indices is None:
        indices = construir_indices_objetos(inventario["jobs"])
        indices_globales["jobs"] = indices
    return buscar_objeto_referenciado(referencia, inventario["jobs"], indices)


def buscar_transformacion_referenciada(referencia: dict[str, Any], inventario: dict[str, Any]) -> dict[str, Any]:
    indices_globales = inventario.setdefault("indices_globales", {})
    indices = indices_globales.get("transformaciones")
    if indices is None:
        indices = construir_indices_objetos(inventario["transformaciones"])
        indices_globales["transformaciones"] = indices
    return buscar_objeto_referenciado(referencia, inventario["transformaciones"], indices)


def analizar_scripts_ejecucion(
    rutas_scripts: list[str | Path],
    resultado: dict[str, Any],
    inventario: dict[str, Any],
) -> dict[str, Any]:
    """Analiza scripts .sh de forma estática y los limita al subgrafo reconstruido."""
    informacion = crear_informacion_scripts_vacia()
    ids_subgrafo = set(resultado.get("nodos", {}))

    for ruta_original in rutas_scripts:
        ruta = Path(ruta_original).expanduser().resolve()
        if ruta.suffix.lower() != ".sh":
            informacion["errores"].append(
                f"Se ignoró {ruta.name}: no tiene extensión .sh."
            )
            continue
        if not ruta.exists() or not ruta.is_file():
            informacion["errores"].append(f"No existe el script: {ruta}")
            continue
        try:
            contenido, codificacion = _leer_texto_script(ruta)
        except Exception as error:
            informacion["errores"].append(
                f"No se pudo leer {ruta.name}: {error}"
            )
            continue

        variables_actuales: dict[str, dict[str, Any]] = {}
        variables_registradas: list[dict[str, Any]] = []
        invocaciones_crudas: list[dict[str, Any]] = []
        variables_credenciales: set[str] = set()

        for linea, tokens in _tokenizar_comandos_shell(contenido):
            indice_kitchen = _indice_kitchen_real(tokens)
            limite_asignaciones = (
                indice_kitchen if indice_kitchen is not None else len(tokens)
            )
            inicio = 1 if tokens and tokens[0] in {
                "export", "readonly", "local", "declare", "typeset"
            } else 0
            for token in tokens[inicio:limite_asignaciones]:
                coincidencia = PATRON_ASIGNACION_SHELL.match(token)
                if not coincidencia:
                    if inicio:
                        continue
                    break
                nombre, expresion = coincidencia.groups()
                registro = {
                    "nombre": nombre,
                    "linea": linea,
                    "expresion": expresion,
                    "variables_referenciadas": extraer_variables_shell(expresion),
                    "tipo": (
                        "EXPRESION_SHELL"
                        if extraer_variables_shell(expresion)
                        or "$(" in expresion
                        or "`" in expresion
                        else "LITERAL_SCRIPT"
                    ),
                }
                variables_registradas.append(registro)
                variables_actuales[nombre] = registro

            if indice_kitchen is None:
                continue
            opciones = _extraer_opciones_kitchen(tokens, indice_kitchen)
            variables_credenciales.update(opciones.pop("variables_credenciales"))
            invocaciones_crudas.append(
                {
                    "linea": linea,
                    **opciones,
                    "_variables": dict(variables_actuales),
                }
            )

        # Si una credencial referencia otra variable, toda la cadena se oculta.
        pendientes = list(variables_credenciales)
        while pendientes:
            nombre = pendientes.pop()
            asignacion = variables_actuales.get(nombre)
            if not asignacion:
                continue
            for referencia in asignacion.get("variables_referenciadas", []):
                if referencia not in variables_credenciales:
                    variables_credenciales.add(referencia)
                    pendientes.append(referencia)

        eventos_archivo: list[dict[str, Any]] = []
        for variable in variables_registradas:
            sensible = variable["nombre"] in variables_credenciales
            if sensible:
                variable.pop("expresion", None)
                variable["valor_oculto"] = True
            eventos_archivo.append(
                {
                    "tipo_evento": "VARIABLE_SHELL",
                    "script": ruta.name,
                    "linea": variable["linea"],
                    "origen": (
                        "[valor oculto]"
                        if sensible
                        else variable.get("expresion", "")
                    ),
                    "destino": variable["nombre"],
                    "origen_tipo": variable["tipo"],
                    "destino_tipo": "VARIABLE_SHELL",
                    "detalle": (
                        "Asignación Shell detectada; valor oculto por uso como credencial."
                        if sensible
                        else "Asignación Shell detectada sin ejecutar el script."
                    ),
                }
            )

        invocaciones: list[dict[str, Any]] = []
        for cruda in invocaciones_crudas:
            job_solicitado = cruda.get("job", "")
            dir_solicitado = cruda.get("dir", "")
            variables_referencia = extraer_variables_shell(
                f"{job_solicitado} {dir_solicitado}"
            )
            busqueda: dict[str, Any] | None = None
            destino: dict[str, Any] | None = None
            if not job_solicitado:
                estado = "job_no_indicado"
                motivo = "La invocación no contiene la opción -job."
            elif variables_referencia:
                estado = "referencia_dinamica"
                motivo = "El Job o la ruta -dir dependen de variables Shell."
            else:
                referencia = {
                    "nombre": job_solicitado,
                    "nombre_normalizado": normalizar_nombre(job_solicitado),
                    "ruta": normalizar_ruta(dir_solicitado) if dir_solicitado else "",
                    "ruta_original": dir_solicitado,
                    "ruta_normalizada": (
                        normalizar_ruta_comparacion(dir_solicitado)
                        if dir_solicitado
                        else ""
                    ),
                    "objeto_id": "",
                    "variables": [],
                    "texto_original": "",
                }
                busqueda = buscar_job_referenciado(referencia, inventario)
                if busqueda.get("estado") == "resuelta":
                    candidato = busqueda["coincidencias"][0]
                    if candidato["id"] in ids_subgrafo:
                        destino = candidato
                        estado = "vinculada_subgrafo"
                        motivo = ""
                    else:
                        estado = "fuera_subgrafo"
                        motivo = (
                            "El Job existe en el XML, pero queda fuera del Job "
                            "principal y su subgrafo."
                        )
                else:
                    estado = busqueda.get("estado", "referencia_no_resuelta")
                    motivo = busqueda.get("motivo", "")

            invocacion = {
                "script": ruta.name,
                "linea": cruda["linea"],
                "job_solicitado": job_solicitado,
                "dir_solicitado": dir_solicitado,
                "estado_vinculacion": estado,
                "motivo": motivo,
                "job_id": destino.get("id") if destino else None,
                "job": destino.get("nombre") if destino else None,
                "ruta_job": destino.get("ruta") if destino else None,
                "parametros": [],
            }
            if destino is not None:
                evento_ejecucion = {
                    "tipo_evento": "SCRIPT_EJECUTA_JOB",
                    "script": ruta.name,
                    "linea": cruda["linea"],
                    "origen": ruta.name,
                    "destino": destino["nombre"],
                    "origen_tipo": "SCRIPT_SHELL",
                    "destino_tipo": "JOB",
                    "proceso_destino_id": destino["id"],
                    "detalle": (
                        f"Invocación estática de kitchen.sh vinculada con "
                        f"{destino['nombre']} [{destino['ruta']}]."
                    ),
                }
                eventos_archivo.append(evento_ejecucion)

                parametros_declarados = {
                    item["nombre"] for item in extraer_parametros_proceso(destino)
                }
                for parametro_crudo in cruda.get("parametros", []):
                    origen = _resolver_origen_parametro_script(
                        parametro_crudo["valor"], cruda["_variables"]
                    )
                    variables_origen = set(origen["variables_shell"])
                    variables_origen.update(origen["variables_intermedias"])
                    sensible = bool(variables_origen & variables_credenciales)
                    if sensible:
                        origen["origen"] = "[valor oculto]"
                        origen["valor_configurado"] = "[valor oculto]"
                        origen["valor_oculto"] = True
                    asignacion = {
                        "script": ruta.name,
                        "ruta_script": str(ruta),
                        "linea": cruda["linea"],
                        "proceso_origen_id": f"SCRIPT::{ruta}",
                        "proceso_origen": ruta.name,
                        "tipo_origen_proceso": "SCRIPT_SHELL",
                        "proceso_destino_id": destino["id"],
                        "proceso_destino": destino["nombre"],
                        "tipo_destino": destino["tipo"],
                        "ruta_destino": destino["ruta"],
                        "parametro_destino": parametro_crudo["nombre"],
                        "parametro_destino_declarado": (
                            parametro_crudo["nombre"] in parametros_declarados
                        ),
                        "estado": "resuelto",
                        "confianza": "alta",
                        "contexto": "kitchen.sh -param",
                        **origen,
                    }
                    invocacion["parametros"].append(asignacion)
                    informacion["asignaciones_parametros"].append(asignacion)

                    for variable_shell in origen["variables_shell"]:
                        eventos_archivo.append(
                            {
                                "tipo_evento": "VARIABLE_SHELL",
                                "script": ruta.name,
                                "linea": cruda["linea"],
                                "origen": variable_shell,
                                "destino": origen["origen"],
                                "origen_tipo": "VARIABLE_SHELL",
                                "destino_tipo": origen["origen_tipo"],
                                "detalle": (
                                    f"{variable_shell} forma parte del origen Shell "
                                    f"de {parametro_crudo['nombre']}."
                                ),
                            }
                        )
                    eventos_archivo.append(
                        {
                            "tipo_evento": origen["origen_tipo"],
                            "script": ruta.name,
                            "linea": cruda["linea"],
                            "origen": origen["origen"],
                            "destino": parametro_crudo["nombre"],
                            "origen_tipo": origen["origen_tipo"],
                            "destino_tipo": "PARAMETRO",
                            "proceso_destino_id": destino["id"],
                            "detalle": (
                                f"Origen Shell de {parametro_crudo['nombre']} "
                                "detectado sin evaluar la expresión."
                            ),
                        }
                    )
                    eventos_archivo.append(
                        {
                            "tipo_evento": "SCRIPT_ASIGNA_PARAMETRO",
                            "script": ruta.name,
                            "linea": cruda["linea"],
                            "origen": origen["origen"],
                            "destino": parametro_crudo["nombre"],
                            "origen_tipo": origen["origen_tipo"],
                            "destino_tipo": "PARAMETRO",
                            "proceso_destino_id": destino["id"],
                            "detalle": (
                                f"{ruta.name} asigna el parámetro "
                                f"{destino['nombre']}.{parametro_crudo['nombre']}."
                            ),
                        }
                    )
            invocaciones.append(invocacion)

        archivo = {
            "nombre": ruta.name,
            "ruta": str(ruta),
            "codificacion": codificacion,
            "analisis": "estatico_sin_ejecucion",
            "variables_shell": variables_registradas,
            "invocaciones": invocaciones,
            "eventos_linaje": eventos_archivo,
            "errores": [],
        }
        informacion["archivos"].append(archivo)
        informacion["eventos_linaje"].extend(eventos_archivo)

    informacion["resumen"] = {
        "archivos_cargados": len(informacion["archivos"]),
        "invocaciones_kitchen": sum(
            len(archivo["invocaciones"]) for archivo in informacion["archivos"]
        ),
        "invocaciones_vinculadas_subgrafo": sum(
            invocacion["estado_vinculacion"] == "vinculada_subgrafo"
            for archivo in informacion["archivos"]
            for invocacion in archivo["invocaciones"]
        ),
        "parametros_asignados": len(informacion["asignaciones_parametros"]),
    }
    return informacion


def crear_nodo_resultado(objeto: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": objeto["id"],
        "nombre": objeto["nombre"],
        "nombre_original": objeto.get("nombre_original", objeto["nombre"]),
        "nombre_normalizado": objeto.get("nombre_normalizado", normalizar_nombre(objeto["nombre"])),
        "tipo": objeto["tipo"],
        "ruta": objeto["ruta"],
        "ruta_original": objeto.get("ruta_original", objeto["ruta"]),
        "ruta_normalizada": objeto.get(
            "ruta_normalizada", normalizar_ruta_comparacion(objeto["ruta"])
        ),
        "ruta_completa": objeto.get(
            "ruta_completa", unir_ruta_y_nombre(objeto["ruta"], objeto["nombre"])
        ),
        "identificador_interno": objeto.get("identificador_interno", ""),
    }


def construir_resumen_catalogo(inventario: dict[str, Any]) -> dict[str, int]:
    return {
        "jobs_total": len(inventario.get("jobs", {})),
        "transformaciones_total": len(inventario.get("transformaciones", {})),
        "carpetas_total": len(inventario.get("carpetas", [])),
    }


def construir_resumen_proceso(
    resultado: dict[str, Any], inventario: dict[str, Any]
) -> dict[str, Any]:
    job_principal = inventario.get("jobs", {}).get(resultado.get("job_principal_id", ""), {})
    nodos = list(resultado.get("nodos", {}).values())
    referencias = resultado.get("referencias_no_resueltas", [])
    referencias_ambiguas = sum(1 for item in referencias if item.get("estado") == "ambigua")
    referencias_dinamicas = sum(
        1 for item in referencias if item.get("estado") == "referencia_dinamica"
    )
    referencias_no_resueltas = sum(
        1 for item in referencias if item.get("estado") == "referencia_no_resuelta"
    )
    informacion_parametros = (
        resultado.get("parametros") or crear_informacion_parametros_vacia()
    )
    informacion_variables = (
        resultado.get("variables") or crear_informacion_variables_vacia()
    )
    declaraciones = informacion_parametros.get("declaraciones", [])
    asignaciones = informacion_parametros.get("asignaciones_entre_procesos", [])
    asignaciones_scripts = informacion_parametros.get(
        "asignaciones_desde_scripts", []
    )
    parametros_contextuales = {
        (item.get("proceso_id", ""), item.get("nombre", ""))
        for item in declaraciones
        if item.get("nombre")
    }
    parametros_contextuales.update(
        (item.get("proceso_destino_id", ""), item.get("parametro_destino", ""))
        for item in [*asignaciones, *asignaciones_scripts]
        if item.get("parametro_destino")
    )
    variables_internas = {
        normalizar_clave_kettle(item.get("variable_destino", ""))
        for item in informacion_variables.get("variables_internas", [])
        if item.get("variable_destino")
    }
    return {
        "job_principal": job_principal.get("nombre", ""),
        "job_principal_id": job_principal.get("id", ""),
        "job_principal_ruta": job_principal.get("ruta", ""),
        "jobs_alcanzables": sum(1 for nodo in nodos if nodo.get("tipo") == "JOB"),
        "transformaciones_alcanzables": sum(
            1 for nodo in nodos if nodo.get("tipo") == "TRANSFORMATION"
        ),
        "nodos_alcanzables": len(nodos),
        "referencias_no_resueltas": referencias_no_resueltas,
        "referencias_ambiguas": referencias_ambiguas,
        "referencias_dinamicas": referencias_dinamicas,
        "referencias_sin_resolver_total": len(referencias),
        "total_parametros": len(parametros_contextuales),
        "total_variables": int(
            resultado.get(
                "total_variables",
                len(obtener_variables_disponibles(informacion_variables)),
            )
        ),
        "total_variables_kettle_usadas": int(
            resultado.get("total_variables_kettle_usadas", 0)
        ),
        "total_variables_internas": len(variables_internas),
        "total_parametros_declarados": len(declaraciones),
        "total_parametros_recibidos": len(asignaciones),
        "total_parametros_enviados": len(asignaciones),
        "total_parametros_desde_scripts": len(asignaciones_scripts),
    }


def crear_nodo_arbol(
    node_id: str,
    nombre: str,
    tipo: str,
    ruta: str,
    estado: str,
    tree_id: str,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "tree_id": tree_id,
        "nombre": nombre,
        "tipo": tipo,
        "ruta": ruta,
        "estado": estado,
        "children": [],
    }


def reconstruir_proceso(
    job_principal_id: str,
    inventario: dict[str, Any],
    rutas_scripts: list[str | Path] | None = None,
) -> dict[str, Any]:
    if job_principal_id not in inventario["jobs"]:
        raise ValueError("El Job principal no se encontró en el inventario.")

    job_raiz = inventario["jobs"][job_principal_id]
    resultado = {
        "job_principal_id": job_principal_id,
        "catalogo_global": construir_resumen_catalogo(inventario),
        "nodos": {job_raiz["id"]: crear_nodo_resultado(job_raiz)},
        "relaciones": [],
        "referencias_no_resueltas": [],
        "referencias_ambiguas": [],
        "referencias_dinamicas": [],
        "arbol": crear_nodo_arbol(
            job_raiz["id"],
            job_raiz["nombre"],
            "JOB",
            job_raiz["ruta"],
            "resuelta",
            f"root::{job_raiz['id']}",
        ),
    }
    reconstruir_recursivo(job_principal_id, inventario, set(), [], resultado, resultado["arbol"])
    try:
        resultado["parametros"] = construir_informacion_parametros(resultado, inventario)
    except Exception as error:
        resultado["parametros"] = crear_informacion_parametros_vacia(str(error))
    try:
        resultado["variables"] = construir_informacion_variables(resultado, inventario)
    except Exception as error:
        resultado["variables"] = crear_informacion_variables_vacia(str(error))
    try:
        resultado["scripts_ejecucion"] = analizar_scripts_ejecucion(
            list(rutas_scripts or []), resultado, inventario
        )
        integrar_scripts_en_parametros(resultado)
    except Exception as error:
        resultado["scripts_ejecucion"] = crear_informacion_scripts_vacia(str(error))
    resultado["proceso_reconstruido"] = construir_resumen_proceso(resultado, inventario)
    return resultado


def reconstruir_recursivo(
    job_id: str,
    inventario: dict[str, Any],
    visitados: set[str],
    pila: list[str],
    resultado: dict[str, Any],
    arbol_actual: dict[str, Any],
) -> None:
    if job_id in pila:
        return

    visitados.add(job_id)
    pila.append(job_id)
    job = inventario["jobs"][job_id]

    for entrada in job.get("entradas", []):
        tipo_esperado = entrada.get("tipo_esperado", "UNKNOWN")
        if tipo_esperado == "UNKNOWN":
            continue

        if not entrada.get("habilitada", True):
            registrar_relacion_desde_entrada(job, entrada, "deshabilitada", None, resultado, arbol_actual)
            continue

        if tipo_esperado == "JOB":
            busqueda = buscar_job_referenciado(entrada_a_referencia(entrada), inventario)
        elif tipo_esperado == "TRANSFORMATION":
            busqueda = buscar_transformacion_referenciada(entrada_a_referencia(entrada), inventario)

        if busqueda["estado"] == "resuelta":
            destino = busqueda["coincidencias"][0]
            estado = "ciclo_detectado" if destino["tipo"] == "JOB" and destino["id"] in pila else "resuelta"
            registrar_relacion_desde_entrada(
                job, entrada, estado, destino, resultado, arbol_actual, busqueda=busqueda
            )
            resultado["nodos"][destino["id"]] = crear_nodo_resultado(destino)
            if destino["tipo"] == "JOB" and estado != "ciclo_detectado":
                hijo = arbol_actual["children"][-1]
                if destino["id"] not in visitados:
                    reconstruir_recursivo(destino["id"], inventario, visitados, pila, resultado, hijo)
        elif busqueda["estado"] == "ambigua":
            registrar_relacion_desde_entrada(
                job,
                entrada,
                "ambigua",
                None,
                resultado,
                arbol_actual,
                busqueda["motivo"],
                busqueda,
            )
        elif busqueda["estado"] == "referencia_dinamica":
            registrar_relacion_desde_entrada(
                job,
                entrada,
                "referencia_dinamica",
                None,
                resultado,
                arbol_actual,
                busqueda["motivo"],
                busqueda,
            )
        else:
            registrar_relacion_desde_entrada(
                job,
                entrada,
                "referencia_no_resuelta",
                None,
                resultado,
                arbol_actual,
                busqueda["motivo"],
                busqueda,
            )

    pila.pop()


def entrada_a_referencia(entrada: dict[str, Any]) -> dict[str, Any]:
    return {
        "nombre": entrada.get("referencia_nombre", ""),
        "nombre_normalizado": entrada.get("referencia_nombre_normalizado", ""),
        "ruta": entrada.get("referencia_ruta", ""),
        "ruta_original": entrada.get("referencia_ruta_original", ""),
        "ruta_normalizada": entrada.get("referencia_ruta_normalizada", ""),
        "ruta_padre": entrada.get("proceso_padre_ruta", ""),
        "objeto_id": entrada.get("referencia_objeto_id", ""),
        "variables": entrada.get("referencia_variables", []),
        "texto_original": entrada.get("referencia_original", ""),
    }


def registrar_relacion_desde_entrada(
    job_origen: dict[str, Any],
    entrada: dict[str, Any],
    estado: str,
    destino: dict[str, Any] | None,
    resultado: dict[str, Any],
    arbol_actual: dict[str, Any],
    motivo: str = "",
    busqueda: dict[str, Any] | None = None,
) -> None:
    destino_nombre = destino["nombre"] if destino else entrada.get("referencia_nombre") or "[sin resolver]"
    destino_tipo = destino["tipo"] if destino else entrada.get("tipo_esperado", "UNKNOWN")

    relacion = {
        "origen": job_origen["nombre"],
        "origen_id": job_origen["id"],
        "origen_ruta": job_origen.get("ruta", ""),
        "origen_tipo": "JOB",
        "destino": destino_nombre,
        "destino_id": destino["id"] if destino else None,
        "destino_ruta": destino.get("ruta", "") if destino else entrada.get("referencia_ruta", ""),
        "destino_tipo": destino_tipo,
        "entrada": entrada.get("nombre", ""),
        "estado": estado,
        "referencia_original": entrada.get("referencia_original", ""),
        "criterio_resolucion": (busqueda or {}).get("criterio", ""),
        "motivo_codigo": (busqueda or {}).get("motivo_codigo", ""),
    }
    resultado["relaciones"].append(relacion)

    nodo_hijo = {
        **crear_nodo_arbol(
            destino["id"] if destino else f"unresolved::{job_origen['id']}::{entrada['indice']}",
            destino_nombre,
            destino_tipo,
            destino["ruta"] if destino else entrada.get("referencia_ruta", ""),
            estado,
            f"{job_origen['id']}::{entrada['indice']}::{estado}",
        ),
        "entrada": entrada.get("nombre", ""),
    }
    arbol_actual["children"].append(nodo_hijo)

    if estado in {"referencia_no_resuelta", "ambigua", "referencia_dinamica"}:
        diagnostico = (busqueda or {}).get("diagnostico", {})
        registro = {
            "job_origen": job_origen["nombre"],
            "proceso_padre": job_origen["nombre"],
            "proceso_padre_id": job_origen["id"],
            "proceso_padre_ruta": job_origen.get("ruta", ""),
            "entrada": entrada.get("nombre", ""),
            "tipo_esperado": entrada.get("tipo_esperado", "UNKNOWN"),
            "referencia_encontrada": entrada.get("referencia_original", ""),
            "nombre_extraido": entrada.get("referencia_nombre", ""),
            "ruta_extraida": entrada.get("referencia_ruta_original", "")
            or entrada.get("referencia_ruta", ""),
            "nombre_normalizado": diagnostico.get(
                "nombre_normalizado", entrada.get("referencia_nombre_normalizado", "")
            ),
            "ruta_normalizada": diagnostico.get(
                "ruta_normalizada", entrada.get("referencia_ruta_normalizada", "")
            ),
            "identificador_repositorio": entrada.get("referencia_objeto_id", ""),
            "coincidencias_por_id": diagnostico.get("coincidencias_por_id", []),
            "coincidencias_por_ruta_nombre": diagnostico.get(
                "coincidencias_por_ruta_nombre", []
            ),
            "coincidencias_por_nombre": diagnostico.get("coincidencias_por_nombre", []),
            "candidatos_ambiguos": diagnostico.get("candidatos_ambiguos", []),
            "rutas_candidatas": diagnostico.get("rutas_candidatas", []),
            "variables_detectadas": diagnostico.get(
                "variables_detectadas", entrada.get("referencia_variables", [])
            ),
            "criterio": (busqueda or {}).get("criterio", ""),
            "estado": estado,
            "resultado_final": estado,
            "motivo_codigo": (busqueda or {}).get("motivo_codigo", ""),
            "motivo": motivo or "No existe en el XML cargado.",
        }
        resultado["referencias_no_resueltas"].append(registro)
        if estado == "ambigua":
            resultado["referencias_ambiguas"].append(registro)
        elif estado == "referencia_dinamica":
            resultado["referencias_dinamicas"].append(registro)


def crear_informacion_parametros_vacia(error: str = "") -> dict[str, Any]:
    return {
        "procesos": [],
        "declaraciones": [],
        "referencias": [],
        "asignaciones_entre_procesos": [],
        "asignaciones_desde_scripts": [],
        "resumen": [],
        "errores": [error] if error else [],
    }


def crear_informacion_variables_vacia(error: str = "") -> dict[str, Any]:
    return {
        "procesos": [],
        "referencias": [],
        "referencias_sin_clasificar": [],
        "get_variables": [],
        "set_variables": [],
        "transiciones": [],
        "variables_internas": [],
        "variables_creadas_renombradas": [],
        "asignaciones_a_parametros": [],
        "resumen": [],
        "errores": [error] if error else [],
    }


def obtener_objetos_subgrafo(
    resultado: dict[str, Any], inventario: dict[str, Any]
) -> list[dict[str, Any]]:
    """Recupera objetos exclusivamente desde la frontera resultado['nodos']."""
    objetos: list[dict[str, Any]] = []
    for proceso_id, nodo in resultado.get("nodos", {}).items():
        coleccion = (
            inventario.get("jobs", {})
            if nodo.get("tipo") == "JOB"
            else inventario.get("transformaciones", {})
        )
        objeto = coleccion.get(proceso_id)
        if objeto is not None:
            objetos.append(objeto)
    job_principal_id = resultado.get("job_principal_id")
    objetos.sort(
        key=lambda item: (
            item.get("id") != job_principal_id,
            item.get("tipo") != "JOB",
            item.get("ruta", ""),
            item.get("nombre", "").lower(),
        )
    )
    return objetos


def obtener_contenedores_parametros_declarados(element: ET.Element) -> list[ET.Element]:
    contenedores: list[ET.Element] = []
    for child in element:
        tag = normalizar_nombre_etiqueta(child.tag)
        if tag == "parameters":
            contenedores.append(child)
        elif tag == "info":
            contenedores.extend(
                sub for sub in child if normalizar_nombre_etiqueta(sub.tag) == "parameters"
            )
    return contenedores


def extraer_parametros_proceso(objeto: dict[str, Any]) -> list[dict[str, Any]]:
    parametros: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for contenedor in obtener_contenedores_parametros_declarados(objeto["xml_element"]):
        for parameter in contenedor:
            if normalizar_nombre_etiqueta(parameter.tag) != "parameter":
                continue
            nombre = normalizar_clave_kettle(
                obtener_texto_hijo_directo(parameter, ("name", "parameter_name"))
            )
            if not nombre or nombre in vistos:
                continue
            vistos.add(nombre)
            elemento_default = next(
                (
                    child
                    for child in parameter
                    if normalizar_nombre_etiqueta(child.tag)
                    in {"default_value", "default", "value"}
                ),
                None,
            )
            parametros.append(
                {
                    "nombre": nombre,
                    "valor_default": (
                        elemento_default.text.strip()
                        if elemento_default is not None and elemento_default.text
                        else ""
                    ),
                    "tiene_valor_default": elemento_default is not None,
                    "descripcion": obtener_texto_hijo_directo(parameter, ("description", "descripcion")),
                    "proceso_id": objeto["id"],
                    "proceso": objeto["nombre"],
                    "tipo_proceso": objeto["tipo"],
                    "ruta": objeto["ruta"],
                }
            )
    return parametros


def extraer_referencias_elemento(objeto: dict[str, Any]) -> list[dict[str, str]]:
    referencias: list[dict[str, str]] = []
    vistos: set[tuple[str, str]] = set()
    for element in objeto["xml_element"].iter():
        contexto = normalizar_nombre_etiqueta(element.tag)
        valores = [(contexto, element.text or "")]
        valores.extend((f"{contexto}@{atributo}", valor) for atributo, valor in element.attrib.items())
        for contexto_valor, valor in valores:
            for coincidencia in PATRON_REFERENCIA_VARIABLE.finditer(valor):
                nombre = normalizar_clave_kettle(
                    coincidencia.group(1) or coincidencia.group(2) or ""
                )
                if not nombre or (nombre, contexto_valor) in vistos:
                    continue
                vistos.add((nombre, contexto_valor))
                referencias.append(
                    {
                        "nombre": nombre,
                        "referencia": coincidencia.group(0),
                        "proceso": objeto["nombre"],
                        "tipo_proceso": objeto["tipo"],
                        "ruta": objeto["ruta"],
                        "contexto": contexto_valor,
                    }
                )
    return referencias


def extraer_referencias_contextuales(objeto: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae referencias conservando proceso, componente y contexto XML exactos."""
    if objeto["tipo"] == "JOB":
        componentes = [
            (
                entrada.get("xml_element"),
                entrada.get("nombre", ""),
                entrada.get("tipo_entrada", ""),
                "ENTRY",
            )
            for entrada in objeto.get("entradas", [])
        ]
    else:
        componentes = [
            (
                step,
                obtener_texto_seguro(step, "name"),
                obtener_texto_seguro(step, "type"),
                "STEP",
            )
            for step in objeto["xml_element"].findall("./step")
        ]

    referencias: list[dict[str, Any]] = []
    vistos: set[tuple[str, str, str, str, str]] = set()
    elementos_componentes: set[int] = set()

    def recorrer(
        raiz: ET.Element,
        componente: str | None,
        tipo_componente: str | None,
        clase_componente: str | None,
        excluidos: set[int] | None = None,
    ) -> None:
        for elemento in raiz.iter():
            if excluidos and id(elemento) in excluidos:
                continue
            contexto = normalizar_nombre_etiqueta(elemento.tag)
            valores = [(contexto, elemento.text or "")]
            valores.extend(
                (f"{contexto}@{atributo}", valor)
                for atributo, valor in elemento.attrib.items()
            )
            for contexto_xml, contenido in valores:
                for coincidencia in PATRON_REFERENCIA_VARIABLE.finditer(contenido):
                    nombre = normalizar_clave_kettle(
                        coincidencia.group(1) or coincidencia.group(2) or ""
                    )
                    firma = (
                        nombre,
                        coincidencia.group(0),
                        componente or "",
                        contexto_xml,
                        contenido.strip(),
                    )
                    if not nombre or firma in vistos:
                        continue
                    vistos.add(firma)
                    referencias.append(
                        {
                            "nombre": nombre,
                            "referencia": coincidencia.group(0),
                            "proceso_id": objeto["id"],
                            "proceso": objeto["nombre"],
                            "tipo_proceso": objeto["tipo"],
                            "ruta": objeto["ruta"],
                            "componente": componente,
                            "tipo_componente": tipo_componente,
                            "clase_componente": clase_componente,
                            "contexto": contexto_xml,
                            "contexto_xml": contexto_xml,
                            "contenido_original": contenido.strip(),
                        }
                    )

    for elemento, nombre, tipo, clase in componentes:
        if elemento is None:
            continue
        elementos_componentes.update(id(sub) for sub in elemento.iter())
        recorrer(elemento, nombre, tipo, clase)
    recorrer(objeto["xml_element"], None, None, None, elementos_componentes)
    return referencias


def clasificar_valor_asignado_parametro(
    valor: str, parametros_declarados_origen: set[str]
) -> dict[str, Any]:
    """Clasifica el origen inmediato de una asignación de llamada explícita."""
    valor = (valor or "").strip()
    referencias = extraer_nombres_referenciados(valor)
    origenes: list[dict[str, str]] = []
    for nombre in referencias:
        tipo = "PARAMETRO" if nombre in parametros_declarados_origen else "VARIABLE"
        origenes.append({"tipo": tipo, "nombre": nombre})

    referencia_directa = len(referencias) == 1 and valor in {
        f"${{{referencias[0]}}}",
        f"%%{referencias[0]}%%",
    }
    if referencia_directa:
        origen_tipo = origenes[0]["tipo"]
        origen = origenes[0]["nombre"]
    elif referencias:
        origen_tipo = "DINAMICO"
        origen = valor
    else:
        origen_tipo = "LITERAL"
        origen = valor
    return {
        "origen_tipo": origen_tipo,
        "origen": origen,
        "origenes": origenes,
        "referencias": referencias,
        "es_referencia_directa": referencia_directa,
    }


def extraer_parametros_llamada(
    job_origen: dict[str, Any],
    entrada: dict[str, Any],
    destino: dict[str, Any],
    *,
    estado: str = "resuelto",
    confianza: str = "alta",
) -> list[dict[str, Any]]:
    entry = entrada.get("xml_element")
    if entry is None:
        return []

    parametros_origen = {
        parametro["nombre"] for parametro in extraer_parametros_proceso(job_origen)
    }
    parametros_destino = (
        {parametro["nombre"] for parametro in extraer_parametros_proceso(destino)}
        if destino.get("xml_element") is not None
        else set()
    )
    asignaciones: list[dict[str, Any]] = []
    vistos: set[tuple[str, str, str]] = set()

    def agregar(
        parametro: str,
        valor: str,
        contexto: str,
        origen_forzado: dict[str, Any] | None = None,
    ) -> None:
        parametro = normalizar_clave_kettle(parametro)
        if not parametro:
            return
        clave = (parametro, valor, contexto)
        if clave in vistos:
            return
        vistos.add(clave)
        origen = origen_forzado or clasificar_valor_asignado_parametro(
            valor, parametros_origen
        )
        asignaciones.append(
            {
                "proceso_origen_id": job_origen["id"],
                "proceso_origen": job_origen["nombre"],
                "tipo_origen": job_origen["tipo"],
                "ruta_origen": job_origen["ruta"],
                "entrada": entrada.get("nombre", ""),
                "tipo_entrada": entrada.get("tipo_entrada", ""),
                "proceso_destino_id": destino["id"],
                "proceso_destino": destino["nombre"],
                "tipo_destino": destino["tipo"],
                "ruta_destino": destino["ruta"],
                "parametro_destino": parametro,
                "parametro_destino_declarado": parametro in parametros_destino,
                "valor_configurado": valor,
                "contexto": contexto,
                "contexto_xml": contexto,
                "estado": estado,
                "confianza": confianza,
                **origen,
            }
        )

    for contenedor in entry.iter():
        if normalizar_nombre_etiqueta(contenedor.tag) not in {"parameters", "arguments"}:
            continue
        for item in contenedor:
            contexto = normalizar_nombre_etiqueta(item.tag)
            if contexto not in {"parameter", "argument"}:
                continue
            parametro = obtener_texto_hijo_directo(
                item, ("name", "parameter_name", "argument_name", "variable_name")
            )
            elementos_valor = [
                child
                for child in item
                if normalizar_nombre_etiqueta(child.tag)
                in {"value", "parameter_value", "argument_value"}
            ]
            elementos_campo = [
                child
                for child in item
                if normalizar_nombre_etiqueta(child.tag) == "stream_name"
            ]
            elemento_valor = next(
                (
                    child
                    for child in elementos_valor
                    if child.text and child.text.strip()
                ),
                elementos_valor[0] if elementos_valor else None,
            )
            elemento_campo = next(
                (
                    child
                    for child in elementos_campo
                    if child.text and child.text.strip()
                ),
                elementos_campo[0] if elementos_campo else None,
            )
            valor_explicito = (
                elemento_valor.text.strip()
                if elemento_valor is not None and elemento_valor.text
                else ""
            )
            campo_flujo = (
                elemento_campo.text.strip()
                if elemento_campo is not None and elemento_campo.text
                else ""
            )
            if valor_explicito or not campo_flujo:
                agregar(
                    parametro,
                    valor_explicito,
                    normalizar_nombre_etiqueta(elemento_valor.tag)
                    if elemento_valor is not None
                    else contexto,
                )
            else:
                agregar(
                    parametro,
                    campo_flujo,
                    "stream_name",
                    {
                        "origen_tipo": "CAMPO_FLUJO",
                        "origen": campo_flujo,
                        "origenes": [
                            {"tipo": "CAMPO_FLUJO", "nombre": campo_flujo}
                        ],
                        "referencias": [],
                        "es_referencia_directa": True,
                    },
                )

    return asignaciones


def crear_destino_llamada_no_resuelto(
    job_origen: dict[str, Any], entrada: dict[str, Any]
) -> dict[str, Any]:
    """Conserva el contexto de parámetros aunque la dependencia no se resuelva."""
    return {
        "id": (
            f"unresolved::{job_origen.get('id', '')}::"
            f"{entrada.get('indice', entrada.get('nombre', ''))}"
        ),
        "nombre": entrada.get("referencia_nombre") or "[sin resolver]",
        "tipo": entrada.get("tipo_esperado", "UNKNOWN"),
        "ruta": entrada.get("referencia_ruta", ""),
        "xml_element": None,
    }


def construir_resumen_parametros(
    procesos: list[dict[str, Any]],
    referencias: list[dict[str, str]],
    asignaciones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    agrupado: dict[str, dict[str, Any]] = {}

    def obtener(nombre: str) -> dict[str, Any]:
        if nombre not in agrupado:
            agrupado[nombre] = {
                "nombre": nombre,
                "declarado_en": [],
                "referenciado_en": [],
                "aparece_en_llamadas": [],
            }
        return agrupado[nombre]

    for proceso in procesos:
        for parametro in proceso["parametros_declarados"]:
            destinos = obtener(parametro["nombre"])["declarado_en"]
            if proceso["nombre"] not in destinos:
                destinos.append(proceso["nombre"])

    for referencia in referencias:
        destinos = obtener(referencia["nombre"])["referenciado_en"]
        if referencia["proceso"] not in destinos:
            destinos.append(referencia["proceso"])

    for asignacion in asignaciones:
        relacion = f"{asignacion['proceso_origen']} -> {asignacion['proceso_destino']}"
        nombres = [asignacion["parametro_destino"]]
        nombres.extend(
            origen.get("nombre", "")
            for origen in asignacion.get("origenes", [])
            if origen.get("tipo") == "PARAMETRO"
        )
        for nombre in nombres:
            if not nombre:
                continue
            relaciones = obtener(nombre)["aparece_en_llamadas"]
            if relacion not in relaciones:
                relaciones.append(relacion)

    return [agrupado[nombre] for nombre in sorted(agrupado, key=lambda item: (item.lower(), item))]


def construir_informacion_parametros(
    resultado: dict[str, Any], inventario: dict[str, Any]
) -> dict[str, Any]:
    informacion = crear_informacion_parametros_vacia()
    ids_incluidos = set(resultado.get("nodos", {}))
    objetos_incluidos = obtener_objetos_subgrafo(resultado, inventario)
    for objeto in objetos_incluidos:
        try:
            parametros = extraer_parametros_proceso(objeto)
        except Exception as error:
            parametros = []
            informacion["errores"].append(
                f"Parámetros de {objeto['tipo']} {objeto['nombre']}: {error}"
            )
        nombres_declarados = {parametro["nombre"] for parametro in parametros}
        try:
            referencias = [
                {**referencia, "clasificacion": "PARAMETRO"}
                for referencia in extraer_referencias_contextuales(objeto)
                if referencia.get("nombre") in nombres_declarados
            ]
        except Exception as error:
            referencias = []
            informacion["errores"].append(
                f"Usos de parámetros de {objeto['tipo']} {objeto['nombre']}: {error}"
            )
        informacion["procesos"].append(
            {
                "id": objeto["id"],
                "nombre": objeto["nombre"],
                "tipo": objeto["tipo"],
                "ruta": objeto["ruta"],
                "parametros_declarados": parametros,
                "referencias": referencias,
                "parametros_recibidos": [],
                "parametros_recibidos_scripts": [],
                "parametros_enviados": [],
            }
        )
        informacion["declaraciones"].extend(parametros)
        informacion["referencias"].extend(referencias)

    for job in (objeto for objeto in objetos_incluidos if objeto["tipo"] == "JOB"):
        for entrada in job.get("entradas", []):
            if not entrada.get("habilitada", True):
                continue
            tipo_esperado = entrada.get("tipo_esperado")
            if tipo_esperado == "JOB":
                busqueda = buscar_job_referenciado(entrada_a_referencia(entrada), inventario)
            elif tipo_esperado == "TRANSFORMATION":
                busqueda = buscar_transformacion_referenciada(entrada_a_referencia(entrada), inventario)
            else:
                continue
            if busqueda.get("estado") == "resuelta":
                destino = busqueda["coincidencias"][0]
                if destino["id"] not in ids_incluidos:
                    continue
                estado_asignacion = "resuelto"
                confianza = "alta"
            else:
                destino = crear_destino_llamada_no_resuelto(job, entrada)
                estado_asignacion = busqueda.get("estado", "referencia_no_resuelta")
                confianza = "media"
            try:
                informacion["asignaciones_entre_procesos"].extend(
                    extraer_parametros_llamada(
                        job,
                        entrada,
                        destino,
                        estado=estado_asignacion,
                        confianza=confianza,
                    )
                )
            except Exception as error:
                informacion["errores"].append(
                    f"Llamada {job['nombre']} -> {destino['nombre']} ({entrada.get('nombre', '')}): {error}"
                )

    procesos_por_id = {
        proceso["id"]: proceso for proceso in informacion.get("procesos", [])
    }
    for asignacion in informacion["asignaciones_entre_procesos"]:
        proceso_origen = procesos_por_id.get(asignacion.get("proceso_origen_id", ""))
        proceso_destino = procesos_por_id.get(asignacion.get("proceso_destino_id", ""))
        if proceso_origen is not None:
            proceso_origen["parametros_enviados"].append(asignacion)
        if proceso_destino is not None:
            proceso_destino["parametros_recibidos"].append(asignacion)

    informacion["resumen"] = construir_resumen_parametros(
        informacion["procesos"],
        informacion["referencias"],
        informacion["asignaciones_entre_procesos"],
    )
    return informacion


def integrar_scripts_en_parametros(resultado: dict[str, Any]) -> None:
    """Añade asignaciones Shell a la vista de parámetros sin tratarlas como llamadas XML."""
    informacion = resultado.get("parametros") or crear_informacion_parametros_vacia()
    scripts = resultado.get("scripts_ejecucion") or crear_informacion_scripts_vacia()
    asignaciones = list(scripts.get("asignaciones_parametros", []))
    informacion["asignaciones_desde_scripts"] = asignaciones
    procesos_por_id = {
        proceso.get("id", ""): proceso
        for proceso in informacion.get("procesos", [])
    }
    for proceso in procesos_por_id.values():
        proceso["parametros_recibidos_scripts"] = []
    for asignacion in asignaciones:
        proceso = procesos_por_id.get(asignacion.get("proceso_destino_id", ""))
        if proceso is not None:
            proceso["parametros_recibidos_scripts"].append(asignacion)
    informacion["resumen"] = construir_resumen_parametros(
        informacion.get("procesos", []),
        informacion.get("referencias", []),
        [
            *informacion.get("asignaciones_entre_procesos", []),
            *asignaciones,
        ],
    )
    resultado["parametros"] = informacion


def obtener_parametros_disponibles(
    informacion: dict[str, Any] | None,
    resultado: dict[str, Any] | None = None,
    inventario: dict[str, Any] | None = None,
) -> list[str]:
    """Devuelve solo declaraciones y destinos explícitos de llamadas."""
    informacion = informacion or crear_informacion_parametros_vacia()
    nombres: set[str] = set()
    for proceso in informacion.get("procesos", []):
        nombres.update(
            parametro["nombre"]
            for parametro in proceso.get("parametros_declarados", [])
            if parametro.get("nombre")
        )
    nombres.update(
        parametro.get("nombre", "")
        for parametro in informacion.get("declaraciones", [])
        if parametro.get("nombre")
    )
    for asignacion in [
        *informacion.get("asignaciones_entre_procesos", []),
        *informacion.get("asignaciones_desde_scripts", []),
    ]:
        if asignacion.get("parametro_destino"):
            nombres.add(asignacion["parametro_destino"])
    nombres_normalizados = {
        normalizar_clave_kettle(nombre)
        for nombre in nombres
        if normalizar_clave_kettle(nombre)
    }
    return sorted(nombres_normalizados, key=lambda nombre: (nombre.lower(), nombre))


def obtener_parametros_contextuales_disponibles(
    informacion: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Devuelve opciones de linaje sin fusionar homónimos de distintos procesos."""
    informacion = informacion or crear_informacion_parametros_vacia()
    contextos: dict[tuple[str, str], dict[str, str]] = {}
    for proceso in informacion.get("procesos", []):
        for parametro in proceso.get("parametros_declarados", []):
            nombre = normalizar_clave_kettle(parametro.get("nombre", ""))
            proceso_id = proceso.get("id", "")
            if nombre:
                contextos[(proceso_id, nombre)] = {
                    "nombre": nombre,
                    "proceso_id": proceso_id,
                    "proceso": proceso.get("nombre", ""),
                    "tipo_proceso": proceso.get("tipo", ""),
                    "ruta": proceso.get("ruta", ""),
                }
    for asignacion in [
        *informacion.get("asignaciones_entre_procesos", []),
        *informacion.get("asignaciones_desde_scripts", []),
    ]:
        nombre = normalizar_clave_kettle(
            asignacion.get("parametro_destino", "")
        )
        proceso_id = asignacion.get("proceso_destino_id", "")
        if not nombre:
            continue
        contextos.setdefault(
            (proceso_id, nombre),
            {
                "nombre": nombre,
                "proceso_id": proceso_id,
                "proceso": asignacion.get("proceso_destino", ""),
                "tipo_proceso": asignacion.get("tipo_destino", ""),
                "ruta": asignacion.get("ruta_destino", ""),
            },
        )

    usados: set[str] = set()
    opciones: list[dict[str, str]] = []
    for (_proceso_id, _nombre), contexto in sorted(
        contextos.items(),
        key=lambda item: (
            item[0][1].lower(),
            item[0][1],
            item[1].get("ruta", ""),
            item[1].get("proceso", "").lower(),
            item[0][0],
        ),
    ):
        ubicacion = contexto.get("ruta") or contexto.get("proceso_id") or "sin contexto"
        etiqueta_base = (
            f"{contexto['nombre']} — "
            f"{contexto.get('proceso') or '[sin resolver]'} [{ubicacion}]"
        )
        etiqueta = etiqueta_base
        if etiqueta in usados:
            etiqueta = f"{etiqueta_base} ({contexto.get('proceso_id', '')})"
        usados.add(etiqueta)
        opciones.append({**contexto, "etiqueta": etiqueta})
    return opciones


def clasificar_referencia(
    referencia: dict[str, Any],
    parametros_declarados: set[str],
    variables_con_evidencia: set[str],
) -> str:
    """Clasifica una referencia por evidencia contextual, nunca solo por formato."""
    nombre = normalizar_clave_kettle(referencia.get("nombre", ""))
    tipo_componente = _normalizar_tipo_pentaho(
        referencia.get("tipo_componente", "")
    )
    if tipo_componente in {"getvariable", "getvariables"}:
        return "VARIABLE"
    if nombre in variables_con_evidencia:
        return "VARIABLE"
    if nombre in parametros_declarados:
        return "PARAMETRO"
    if referencia.get("clase_componente") in {"STEP", "ENTRY"}:
        return "VARIABLE"
    return "REFERENCIA_SIN_CLASIFICAR"


def construir_resumen_variables(
    referencias: list[dict[str, Any]],
    transiciones: list[dict[str, Any]],
    asignaciones_a_parametros: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    agrupado: dict[str, dict[str, Any]] = {}

    def obtener(nombre: str) -> dict[str, Any]:
        nombre = normalizar_clave_kettle(nombre)
        return agrupado.setdefault(
            nombre,
            {
                "nombre": nombre,
                "procesos": [],
                "evidencias": [],
                "componentes": [],
                "parametros_alimentados": [],
            },
        )

    def registrar(nombre: str, proceso: str, evidencia: str, componente: str = "") -> None:
        if not normalizar_clave_kettle(nombre):
            return
        item = obtener(nombre)
        if proceso and proceso not in item["procesos"]:
            item["procesos"].append(proceso)
        if evidencia and evidencia not in item["evidencias"]:
            item["evidencias"].append(evidencia)
        if componente and componente not in item["componentes"]:
            item["componentes"].append(componente)

    for referencia in referencias:
        registrar(
            referencia.get("nombre", ""),
            referencia.get("proceso", ""),
            "REFERENCIA",
            referencia.get("componente") or referencia.get("contexto_xml", ""),
        )
    for transicion in transiciones:
        if transicion.get("origen_tipo") == "VARIABLE":
            registrar(
                transicion.get("variable_origen", ""),
                transicion.get("proceso", ""),
                transicion.get("tipo_evento", "TRANSICION"),
                transicion.get("componente", ""),
            )
        if transicion.get("destino_tipo") == "VARIABLE":
            registrar(
                transicion.get("variable_destino", ""),
                transicion.get("proceso", ""),
                transicion.get("tipo_evento", "TRANSICION"),
                transicion.get("componente", ""),
            )
    for asignacion in asignaciones_a_parametros:
        nombre = asignacion.get("variable", "")
        registrar(
            nombre,
            asignacion.get("proceso_origen", ""),
            "VARIABLE_ASIGNA_PARAMETRO",
            asignacion.get("entrada", ""),
        )
        relacion = (
            f"{asignacion.get('proceso_destino', '')}:"
            f"{asignacion.get('parametro_destino', '')}"
        )
        item = obtener(nombre)
        if relacion not in item["parametros_alimentados"]:
            item["parametros_alimentados"].append(relacion)
    return [
        agrupado[nombre]
        for nombre in sorted(agrupado, key=lambda item: (item.lower(), item))
        if nombre
    ]


def construir_informacion_variables(
    resultado: dict[str, Any], inventario: dict[str, Any]
) -> dict[str, Any]:
    informacion = crear_informacion_variables_vacia()
    objetos_incluidos = obtener_objetos_subgrafo(resultado, inventario)
    for objeto in objetos_incluidos:
        try:
            parametros_declarados = {
                parametro["nombre"] for parametro in extraer_parametros_proceso(objeto)
            }
            transiciones_todas = extraer_transiciones_linaje(objeto)
            variables_con_evidencia = {
                normalizar_clave_kettle(transicion.get("variable_origen", ""))
                for transicion in transiciones_todas
                if transicion.get("origen_tipo") == "VARIABLE"
            }
            variables_con_evidencia.update(
                normalizar_clave_kettle(transicion.get("variable_destino", ""))
                for transicion in transiciones_todas
                if transicion.get("destino_tipo") == "VARIABLE"
            )
            referencias_variables: list[dict[str, Any]] = []
            referencias_sin_clasificar: list[dict[str, Any]] = []
            for referencia in extraer_referencias_contextuales(objeto):
                clasificacion = clasificar_referencia(
                    referencia, parametros_declarados, variables_con_evidencia
                )
                registro = {**referencia, "clasificacion": clasificacion}
                if clasificacion == "VARIABLE":
                    referencias_variables.append(registro)
                elif clasificacion == "REFERENCIA_SIN_CLASIFICAR":
                    referencias_sin_clasificar.append(registro)

            transiciones = list(transiciones_todas)
            get_variables = [
                transicion
                for transicion in transiciones
                if transicion.get("tipo_evento") == "GET_VARIABLE"
            ]
            set_variables = [
                transicion
                for transicion in transiciones
                if transicion.get("tipo_evento") == "SET_VARIABLE"
            ]
            variables_internas = [
                transicion
                for transicion in transiciones
                if transicion.get("destino_tipo") == "VARIABLE"
                and transicion.get("tipo_evento")
                in {"ASIGNACION_INTERNA", "SET_VARIABLE"}
            ]
            creadas_renombradas = [
                transicion
                for transicion in transiciones
                if transicion.get("destino_tipo") == "VARIABLE"
                and transicion.get("tipo_evento")
                in {"ASIGNACION_INTERNA", "DERIVACION", "RENOMBRE", "SET_VARIABLE"}
                and transicion.get("variable_destino")
            ]
        except Exception as error:
            referencias_variables = []
            referencias_sin_clasificar = []
            transiciones = []
            get_variables = []
            set_variables = []
            variables_internas = []
            creadas_renombradas = []
            informacion["errores"].append(
                f"Variables de {objeto['tipo']} {objeto['nombre']}: {error}"
            )

        proceso = {
            "id": objeto["id"],
            "nombre": objeto["nombre"],
            "tipo": objeto["tipo"],
            "ruta": objeto["ruta"],
            "referencias": referencias_variables,
            "referencias_sin_clasificar": referencias_sin_clasificar,
            "get_variables": get_variables,
            "set_variables": set_variables,
            "transiciones": transiciones,
            "variables_internas": variables_internas,
            "variables_creadas_renombradas": creadas_renombradas,
        }
        informacion["procesos"].append(proceso)
        for clave in (
            "referencias",
            "referencias_sin_clasificar",
            "get_variables",
            "set_variables",
            "transiciones",
            "variables_internas",
            "variables_creadas_renombradas",
        ):
            informacion[clave].extend(proceso[clave])

    parametros = resultado.get("parametros") or crear_informacion_parametros_vacia()
    for asignacion in parametros.get("asignaciones_entre_procesos", []):
        for origen in asignacion.get("origenes", []):
            if origen.get("tipo") != "VARIABLE" or not origen.get("nombre"):
                continue
            informacion["asignaciones_a_parametros"].append(
                {
                    **asignacion,
                    "variable": origen["nombre"],
                    "evento": "VARIABLE_ASIGNA_PARAMETRO",
                }
            )

    informacion["resumen"] = construir_resumen_variables(
        informacion["referencias"],
        informacion["transiciones"],
        informacion["asignaciones_a_parametros"],
    )
    return informacion


def obtener_variables_disponibles(
    informacion: dict[str, Any] | None,
    propiedades_kettle: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Devuelve solo nombres con evidencia de variable dentro del subgrafo."""
    informacion = informacion or crear_informacion_variables_vacia()
    nombres: set[str] = set()
    for referencia in informacion.get("referencias", []):
        if referencia.get("clasificacion") in {
            "VARIABLE",
            "VARIABLE_KETTLE",
        } and referencia.get("nombre"):
            nombres.add(referencia["nombre"])
    for transicion in informacion.get("transiciones", []):
        if transicion.get("origen_tipo") == "VARIABLE" and transicion.get("variable_origen"):
            nombres.add(transicion["variable_origen"])
        if transicion.get("destino_tipo") == "VARIABLE" and transicion.get("variable_destino"):
            nombres.add(transicion["variable_destino"])
    nombres.update(
        item.get("variable", "")
        for item in informacion.get("asignaciones_a_parametros", [])
        if item.get("variable")
    )
    if propiedades_kettle:
        indice_kettle = crear_indice_kettle_normalizado(propiedades_kettle)
        nombres.update(
            referencia.get("nombre", "")
            for referencia in informacion.get("referencias_sin_clasificar", [])
            if buscar_variable_en_kettle(
                referencia.get("nombre", ""), indice_kettle
            )
            is not None
        )
    nombres_normalizados = {
        normalizar_clave_kettle(nombre)
        for nombre in nombres
        if normalizar_clave_kettle(nombre)
    }
    return sorted(nombres_normalizados, key=lambda nombre: (nombre.lower(), nombre))


def aplicar_clasificacion_kettle_variables(
    informacion: dict[str, Any] | None,
    propiedades_kettle: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Promueve o revierte referencias según el Kettle cargado actualmente."""
    informacion = informacion or crear_informacion_variables_vacia()
    indice_kettle = crear_indice_kettle_normalizado(propiedades_kettle or {})

    def clave(referencia: dict[str, Any]) -> tuple[str, ...]:
        return (
            referencia.get("proceso_id", ""),
            normalizar_clave_kettle(referencia.get("nombre", "")),
            referencia.get("componente", ""),
            referencia.get("contexto_xml", ""),
            referencia.get("referencia", ""),
        )

    referencias_globales: list[dict[str, Any]] = []
    sin_clasificar_globales: list[dict[str, Any]] = []
    for proceso in informacion.get("procesos", []):
        referencias_fijas = [
            referencia
            for referencia in proceso.get("referencias", [])
            if referencia.get("clasificacion") != "VARIABLE_KETTLE"
        ]
        candidatos = list(proceso.get("referencias_sin_clasificar", []))
        candidatos.extend(
            referencia
            for referencia in proceso.get("referencias", [])
            if referencia.get("clasificacion") == "VARIABLE_KETTLE"
        )
        referencias = list(referencias_fijas)
        sin_clasificar: list[dict[str, Any]] = []
        claves_referencias = {clave(item) for item in referencias}
        claves_sin_clasificar: set[tuple[str, ...]] = set()
        for referencia in candidatos:
            nombre = normalizar_clave_kettle(referencia.get("nombre", ""))
            if buscar_variable_en_kettle(nombre, indice_kettle) is not None:
                promovida = {
                    **referencia,
                    "clasificacion": "VARIABLE_KETTLE",
                    "evidencia_clasificacion": "kettle.properties",
                }
                if clave(promovida) not in claves_referencias:
                    referencias.append(promovida)
                    claves_referencias.add(clave(promovida))
            else:
                pendiente = {
                    **referencia,
                    "clasificacion": "REFERENCIA_SIN_CLASIFICAR",
                }
                pendiente.pop("evidencia_clasificacion", None)
                if clave(pendiente) not in claves_sin_clasificar:
                    sin_clasificar.append(pendiente)
                    claves_sin_clasificar.add(clave(pendiente))
        proceso["referencias"] = referencias
        proceso["referencias_sin_clasificar"] = sin_clasificar
        referencias_globales.extend(referencias)
        sin_clasificar_globales.extend(sin_clasificar)

    informacion["referencias"] = referencias_globales
    informacion["referencias_sin_clasificar"] = sin_clasificar_globales
    informacion["resumen"] = construir_resumen_variables(
        informacion["referencias"],
        informacion.get("transiciones", []),
        informacion.get("asignaciones_a_parametros", []),
    )
    return informacion


def buscar_referencias_en_elemento(
    element: ET.Element,
    parametro: str,
    proceso: dict[str, Any],
    componente: str | None,
    tipo_componente: str | None,
    clase_componente: str | None,
    rutas_ocurrencia: list[list[str]],
    elementos_excluidos: set[int] | None = None,
) -> list[dict[str, Any]]:
    parametro = normalizar_clave_kettle(parametro)
    apariciones: list[dict[str, Any]] = []
    vistas: set[tuple[str, str, str]] = set()
    for subelement in element.iter():
        if elementos_excluidos and id(subelement) in elementos_excluidos:
            continue
        etiqueta = normalizar_nombre_etiqueta(subelement.tag)
        valores = [(etiqueta, subelement.text or "")]
        valores.extend(
            (f"{etiqueta}@{atributo}", valor)
            for atributo, valor in subelement.attrib.items()
        )
        for contexto, contenido in valores:
            for coincidencia in PATRON_REFERENCIA_VARIABLE.finditer(contenido):
                nombre = normalizar_clave_kettle(
                    coincidencia.group(1) or coincidencia.group(2) or ""
                )
                if nombre != parametro:
                    continue
                clave = (coincidencia.group(0), contexto, contenido)
                if clave in vistas:
                    continue
                vistas.add(clave)
                apariciones.append(
                    {
                        "parametro": parametro,
                        "tipo_aparicion": "REFERENCIA",
                        "proceso_id": proceso["id"],
                        "proceso": proceso["nombre"],
                        "tipo_proceso": proceso["tipo"],
                        "ruta": proceso["ruta"],
                        "rutas_ocurrencia": rutas_ocurrencia,
                        "componente": componente,
                        "tipo_componente": tipo_componente,
                        "clase_componente": clase_componente,
                        "referencia_original": coincidencia.group(0),
                        "contexto_xml": contexto,
                        "contenido_original": contenido.strip(),
                    }
                )
    return apariciones


def localizar_referencias_en_componentes(
    proceso: dict[str, Any], parametro: str, rutas_ocurrencia: list[list[str]]
) -> tuple[list[dict[str, Any]], set[int]]:
    apariciones: list[dict[str, Any]] = []
    elementos_componentes: set[int] = set()
    if proceso["tipo"] == "JOB":
        componentes = [
            (
                entrada.get("xml_element"),
                entrada.get("nombre", ""),
                entrada.get("tipo_entrada", ""),
                "ENTRY",
            )
            for entrada in proceso.get("entradas", [])
        ]
    else:
        componentes = [
            (
                step,
                obtener_texto_seguro(step, "name"),
                obtener_texto_seguro(step, "type"),
                "STEP",
            )
            for step in proceso["xml_element"].findall("./step")
        ]

    for indice, (element, nombre, tipo, clase) in enumerate(componentes, start=1):
        if element is None:
            continue
        elementos_componentes.update(id(sub) for sub in element.iter())
        apariciones.extend(
            buscar_referencias_en_elemento(
                element,
                parametro,
                proceso,
                nombre or f"{clase.lower()}_{indice}",
                tipo,
                clase,
                rutas_ocurrencia,
            )
        )
    return apariciones, elementos_componentes


def localizar_parametro_en_llamadas(
    proceso: dict[str, Any],
    parametro: str,
    asignaciones: list[dict[str, Any]],
    rutas_ocurrencia: list[list[str]],
) -> list[dict[str, Any]]:
    parametro = normalizar_clave_kettle(parametro)
    apariciones: list[dict[str, Any]] = []
    tipos_entrada = {
        entrada.get("nombre", ""): entrada.get("tipo_entrada", "")
        for entrada in proceso.get("entradas", [])
    }
    for asignacion in asignaciones:
        destino = normalizar_clave_kettle(
            asignacion.get("parametro_destino", "")
        )
        origenes_parametro = {
            normalizar_clave_kettle(origen.get("nombre", ""))
            for origen in asignacion.get("origenes", [])
            if origen.get("tipo") == "PARAMETRO"
        }
        es_origen = asignacion.get("proceso_origen_id") == proceso["id"]
        es_destino = asignacion.get("proceso_destino_id") == proceso["id"]
        tipos_aparicion: list[str] = []
        if es_origen and destino == parametro:
            tipos_aparicion.append("PARAMETRO_ENVIADO")
        if es_origen and parametro in origenes_parametro:
            tipos_aparicion.append("PARAMETRO_ORIGEN_LLAMADA")
        if es_destino and destino == parametro:
            tipos_aparicion.append("PARAMETRO_RECIBIDO")
        for tipo_aparicion in tipos_aparicion:
            apariciones.append(
                {
                    "parametro": parametro,
                    "tipo_aparicion": tipo_aparicion,
                    "proceso_id": proceso["id"],
                    "proceso": proceso["nombre"],
                    "tipo_proceso": proceso["tipo"],
                    "ruta": proceso["ruta"],
                    "rutas_ocurrencia": rutas_ocurrencia,
                    "componente": asignacion.get("entrada", ""),
                    "tipo_componente": tipos_entrada.get(
                        asignacion.get("entrada", ""),
                        asignacion.get("tipo_entrada", ""),
                    ),
                    "clase_componente": "ENTRY",
                    "referencia_original": None,
                    "contexto_xml": asignacion.get(
                        "contexto_xml", asignacion.get("contexto", "parameter")
                    ),
                    "proceso_origen": asignacion.get("proceso_origen", ""),
                    "proceso_destino": asignacion.get("proceso_destino", ""),
                    "tipo_destino": asignacion.get("tipo_destino", ""),
                    "ruta_destino": asignacion.get("ruta_destino", ""),
                    "parametro_destino": asignacion.get("parametro_destino", ""),
                    "valor_configurado": asignacion.get("valor_configurado", ""),
                    "origen_tipo": asignacion.get("origen_tipo", ""),
                    "origen": asignacion.get("origen", ""),
                    "origenes": asignacion.get("origenes", []),
                }
            )
    return apariciones


def localizar_parametro(
    parametro: str,
    resultado: dict[str, Any],
    inventario: dict[str, Any],
) -> dict[str, Any]:
    parametro = normalizar_clave_kettle(parametro)
    informacion = resultado.get("parametros") or crear_informacion_parametros_vacia()
    procesos_parametros = {
        proceso["id"]: proceso for proceso in informacion.get("procesos", [])
    }
    try:
        ocurrencias = construir_ocurrencias_proceso(
            resultado["arbol"], inventario, set(resultado.get("nodos", {}))
        )
    except Exception:
        ocurrencias = []
    rutas_por_proceso: dict[str, list[list[str]]] = {}
    for ocurrencia in ocurrencias:
        ruta = ocurrencia.get("ruta_ocurrencia", [])
        rutas = rutas_por_proceso.setdefault(ocurrencia["proceso_id"], [])
        if ruta and ruta not in rutas:
            rutas.append(ruta)

    apariciones: list[dict[str, Any]] = []
    errores: list[str] = []
    asignaciones = informacion.get("asignaciones_entre_procesos", [])
    for proceso_id, nodo in resultado.get("nodos", {}).items():
        proceso = obtener_objeto_inventario(nodo["tipo"], proceso_id, inventario)
        if not proceso:
            continue
        rutas = rutas_por_proceso.get(proceso_id, [])
        datos_parametros = procesos_parametros.get(proceso_id, {})
        for declarado in datos_parametros.get("parametros_declarados", []):
            if normalizar_clave_kettle(declarado.get("nombre", "")) != parametro:
                continue
            apariciones.append(
                {
                    "parametro": parametro,
                    "tipo_aparicion": "DECLARACION",
                    "proceso_id": proceso_id,
                    "proceso": proceso["nombre"],
                    "tipo_proceso": proceso["tipo"],
                    "ruta": proceso["ruta"],
                    "rutas_ocurrencia": rutas,
                    "componente": None,
                    "tipo_componente": None,
                    "clase_componente": None,
                    "referencia_original": None,
                    "contexto_xml": "parameters",
                    "valor_default": declarado.get("valor_default", ""),
                    "descripcion": declarado.get("descripcion", ""),
                }
            )

        try:
            apariciones.extend(
                {
                    **referencia,
                    "parametro": parametro,
                    "tipo_aparicion": "USO_PARAMETRO",
                    "rutas_ocurrencia": rutas,
                    "referencia_original": referencia.get("referencia", ""),
                }
                for referencia in datos_parametros.get("referencias", [])
                if normalizar_clave_kettle(referencia.get("nombre", ""))
                == parametro
            )
            apariciones.extend(
                localizar_parametro_en_llamadas(
                    proceso, parametro, asignaciones, rutas
                )
            )
        except Exception as error:
            errores.append(f"{proceso['tipo']} {proceso['nombre']}: {error}")

    for asignacion in informacion.get("asignaciones_desde_scripts", []):
        if (
            normalizar_clave_kettle(asignacion.get("parametro_destino", ""))
            != parametro
        ):
            continue
        proceso_id = asignacion.get("proceso_destino_id", "")
        apariciones.append(
            {
                "parametro": parametro,
                "tipo_aparicion": "SCRIPT_ASIGNA_PARAMETRO",
                "proceso_id": proceso_id,
                "proceso": asignacion.get("proceso_destino", ""),
                "tipo_proceso": asignacion.get("tipo_destino", "JOB"),
                "ruta": asignacion.get("ruta_destino", ""),
                "rutas_ocurrencia": rutas_por_proceso.get(proceso_id, []),
                "componente": asignacion.get("script", ""),
                "tipo_componente": "SCRIPT_SHELL",
                "clase_componente": "kitchen.sh",
                "referencia_original": asignacion.get("origen", ""),
                "contexto_xml": "kitchen.sh -param",
                "origen_tipo": asignacion.get("origen_tipo", ""),
                "variables_shell": asignacion.get("variables_shell", []),
                "linea": asignacion.get("linea"),
            }
        )

    return {
        "parametro_seleccionado": parametro,
        "apariciones": apariciones,
        "errores": errores,
    }


def localizar_variable(
    variable: str,
    resultado: dict[str, Any],
    inventario: dict[str, Any],
    propiedades_kettle: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Localiza evidencia puntual sin reconstruir el linaje causal completo."""
    variable = normalizar_clave_kettle(variable)
    informacion = resultado.get("variables")
    if informacion is None:
        informacion = construir_informacion_variables(resultado, inventario)
    try:
        ocurrencias = construir_ocurrencias_proceso(
            resultado["arbol"], inventario, set(resultado.get("nodos", {}))
        )
    except Exception:
        ocurrencias = []
    rutas_por_proceso: dict[str, list[list[str]]] = {}
    for ocurrencia in ocurrencias:
        ruta = ocurrencia.get("ruta_ocurrencia", [])
        rutas = rutas_por_proceso.setdefault(ocurrencia.get("proceso_id", ""), [])
        if ruta and ruta not in rutas:
            rutas.append(ruta)

    apariciones: list[dict[str, Any]] = []
    errores: list[str] = []
    for proceso in informacion.get("procesos", []):
        proceso_id = proceso.get("id", "")
        rutas = rutas_por_proceso.get(proceso_id, [])
        for referencia in proceso.get("referencias", []):
            if normalizar_clave_kettle(referencia.get("nombre", "")) != variable:
                continue
            apariciones.append(
                {
                    **referencia,
                    "variable": variable,
                    "tipo_aparicion": "REFERENCIA_VARIABLE",
                    "referencia_original": referencia.get("referencia", ""),
                    "rutas_ocurrencia": rutas,
                }
            )
        if propiedades_kettle:
            indice_kettle = crear_indice_kettle_normalizado(propiedades_kettle)
            for referencia in proceso.get("referencias_sin_clasificar", []):
                if normalizar_clave_kettle(referencia.get("nombre", "")) != variable:
                    continue
                if buscar_variable_en_kettle(variable, indice_kettle) is None:
                    continue
                apariciones.append(
                    {
                        **referencia,
                        "variable": variable,
                        "clasificacion": "VARIABLE_KETTLE",
                        "tipo_aparicion": "REFERENCIA_VARIABLE_KETTLE",
                        "referencia_original": referencia.get("referencia", ""),
                        "rutas_ocurrencia": rutas,
                    }
                )
        for transicion in proceso.get("transiciones", []):
            coincide_origen = (
                transicion.get("origen_tipo") == "VARIABLE"
                and normalizar_clave_kettle(
                    transicion.get("variable_origen", "")
                )
                == variable
            )
            coincide_destino = (
                transicion.get("destino_tipo") == "VARIABLE"
                and normalizar_clave_kettle(
                    transicion.get("variable_destino", "")
                )
                == variable
            )
            if not coincide_origen and not coincide_destino:
                continue
            apariciones.append(
                {
                    **transicion,
                    "variable": variable,
                    "tipo_aparicion": transicion.get(
                        "tipo_evento", "TRANSICION_VARIABLE"
                    ),
                    "clase_componente": (
                        "ENTRY" if proceso.get("tipo") == "JOB" else "STEP"
                    ),
                    "contexto_xml": transicion.get("contexto_xml", ""),
                    "rutas_ocurrencia": rutas,
                }
            )

    for asignacion in informacion.get("asignaciones_a_parametros", []):
        if normalizar_clave_kettle(asignacion.get("variable", "")) != variable:
            continue
        apariciones.append(
            {
                **asignacion,
                "variable": variable,
                "tipo_aparicion": "VARIABLE_ASIGNA_PARAMETRO",
                "proceso_id": asignacion.get("proceso_origen_id", ""),
                "proceso": asignacion.get("proceso_origen", ""),
                "tipo_proceso": asignacion.get("tipo_origen", ""),
                "ruta": asignacion.get("ruta_origen", ""),
                "componente": asignacion.get("entrada", ""),
                "tipo_componente": asignacion.get("tipo_entrada", ""),
                "clase_componente": "ENTRY",
                "rutas_ocurrencia": rutas_por_proceso.get(
                    asignacion.get("proceso_origen_id", ""), []
                ),
            }
        )

    if propiedades_kettle:
        indice = crear_indice_kettle_normalizado(propiedades_kettle)
        propiedad = buscar_variable_en_kettle(variable, indice)
        if propiedad is not None:
            segura = _datos_kettle_seguros(propiedad)
            apariciones.insert(
                0,
                {
                    "variable": variable,
                    "tipo_aparicion": "ORIGEN_KETTLE",
                    "proceso_id": "",
                    "proceso": segura.get("archivo", "kettle.properties"),
                    "tipo_proceso": "KETTLE_PROPERTIES",
                    "ruta": segura.get("ruta", ""),
                    "componente": f"línea {segura.get('linea', '')}",
                    "tipo_componente": "PROPIEDAD",
                    "clase_componente": "PROPIEDAD",
                    "contexto_xml": "",
                    "valor_enmascarado": segura.get("valor_enmascarado", ""),
                    "rutas_ocurrencia": [],
                },
            )
    return {
        "variable_seleccionada": variable,
        "apariciones": apariciones,
        "errores": errores,
    }


def extraer_asignaciones_estaticas(objeto: dict[str, Any]) -> list[dict[str, Any]]:
    """Detecta asignaciones explícitas conservadoras dentro de entries y steps."""
    if objeto["tipo"] == "JOB":
        componentes = [
            (entrada.get("xml_element"), entrada.get("nombre", ""), entrada.get("tipo_entrada", ""))
            for entrada in objeto.get("entradas", [])
        ]
    else:
        componentes = [
            (step, obtener_texto_seguro(step, "name"), obtener_texto_seguro(step, "type"))
            for step in objeto["xml_element"].findall("./step")
        ]

    asignaciones: list[dict[str, Any]] = []
    vistas: set[tuple[str, str, str, str]] = set()
    tags_destino = {"variable_name"}
    tags_valor = {
        "value", "variable_value", "parameter_value", "argument_value",
        "default_value", "field_name", "stream_name", "formula", "expression",
    }
    for raiz, componente, tipo_componente in componentes:
        if raiz is None:
            continue
        for contenedor in raiz.iter():
            hijos = list(contenedor)
            if not hijos:
                continue
            destino = normalizar_clave_kettle(next(
                (
                    (hijo.text or "").strip()
                    for hijo in hijos
                    if normalizar_nombre_etiqueta(hijo.tag) in tags_destino
                    and (hijo.text or "").strip()
                ),
                "",
            ))
            tag_contenedor = normalizar_nombre_etiqueta(contenedor.tag)
            # Los parámetros de entries de llamada ya los analiza
            # extraer_parametros_llamada con semántica origen/destino.
            if objeto["tipo"] == "JOB" and tag_contenedor in {"parameter", "argument"}:
                continue
            if not destino and tag_contenedor in {"variable", "parameter", "argument"}:
                destino = normalizar_clave_kettle(
                    obtener_texto_hijo_directo(contenedor, ("name",))
                )
            if not destino:
                continue
            elemento_valor = next(
                (
                    hijo
                    for hijo in hijos
                    if normalizar_nombre_etiqueta(hijo.tag) in tags_valor
                ),
                None,
            )
            valor = (
                (elemento_valor.text or "").strip()
                if elemento_valor is not None
                else ""
            )
            clave = (componente, destino, valor, tag_contenedor)
            if clave in vistas:
                continue
            vistas.add(clave)
            asignaciones.append(
                {
                    "proceso_id": objeto["id"],
                    "proceso": objeto["nombre"],
                    "tipo_proceso": objeto["tipo"],
                    "ruta": objeto["ruta"],
                    "componente": componente,
                    "tipo_componente": tipo_componente,
                    "variable_destino": destino,
                    "valor": valor,
                    "tiene_valor_configurado": elemento_valor is not None,
                    "referencias": extraer_nombres_referenciados(valor),
                    "contexto_xml": tag_contenedor,
                }
            )
    return asignaciones


def _normalizar_tipo_pentaho(tipo: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (tipo or "").lower())


def _iterar_componentes_linaje(
    objeto: dict[str, Any],
) -> list[tuple[ET.Element, str, str]]:
    if objeto["tipo"] == "JOB":
        return [
            (entrada["xml_element"], entrada.get("nombre", ""), entrada.get("tipo_entrada", ""))
            for entrada in objeto.get("entradas", [])
            if entrada.get("xml_element") is not None
        ]
    return [
        (
            step,
            obtener_texto_seguro(step, "name"),
            obtener_texto_seguro(step, "type"),
        )
        for step in objeto["xml_element"]
        if normalizar_nombre_etiqueta(step.tag) == "step"
    ]


def _iterar_campos_configuracion(elemento: ET.Element) -> list[ET.Element]:
    campos: list[ET.Element] = []
    for contenedor in elemento.iter():
        if normalizar_nombre_etiqueta(contenedor.tag) not in {"fields", "variables"}:
            continue
        campos.extend(
            child
            for child in contenedor
            if normalizar_nombre_etiqueta(child.tag) in {"field", "variable"}
        )
    return campos


def _extraer_campos_resultado_dinamico(elemento: ET.Element) -> list[str]:
    campos: list[str] = []
    for contenedor in elemento.iter():
        if normalizar_nombre_etiqueta(contenedor.tag) not in {"row-meta", "row_meta"}:
            continue
        for meta in contenedor:
            if normalizar_nombre_etiqueta(meta.tag) not in {"value-meta", "value_meta"}:
                continue
            nombre = obtener_texto_hijo_directo(meta, ("name", "field_name"))
            if nombre and nombre not in campos:
                campos.append(nombre)
    return campos


def extraer_transiciones_linaje(objeto: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae aristas semánticas de variables y campos sin mezclar filas XML."""
    transiciones: list[dict[str, Any]] = []
    componentes_semanticos: set[tuple[str, str]] = set()
    parametros_declarados = {
        parametro["nombre"] for parametro in extraer_parametros_proceso(objeto)
    }

    def agregar(
        tipo_evento: str,
        componente: str,
        tipo_componente: str,
        origen: str,
        destino: str,
        origen_tipo: str,
        destino_tipo: str,
        detalle: str,
        **extras: Any,
    ) -> None:
        if not destino:
            return
        transiciones.append(
            {
                "tipo_evento": tipo_evento,
                "proceso_id": objeto["id"],
                "proceso": objeto["nombre"],
                "tipo_proceso": objeto["tipo"],
                "ruta": objeto["ruta"],
                "componente": componente,
                "tipo_componente": tipo_componente,
                "variable_origen": origen,
                "variable_destino": destino,
                "origen_tipo": origen_tipo,
                "destino_tipo": destino_tipo,
                "origen_proceso_id": extras.pop("origen_proceso_id", objeto["id"]),
                "destino_proceso_id": extras.pop("destino_proceso_id", objeto["id"]),
                "detalle": detalle,
                "estado": "resuelto",
                **extras,
            }
        )

    for elemento, componente, tipo_componente in _iterar_componentes_linaje(objeto):
        tipo_normalizado = _normalizar_tipo_pentaho(tipo_componente)
        if tipo_normalizado in {
            "tableinput",
            "execsqlrow",
            "dynamicssqlrow",
        }:
            for campo_salida in _extraer_campos_resultado_dinamico(elemento):
                agregar(
                    "RESULTADO_SQL",
                    componente,
                    tipo_componente,
                    f"resultado SQL de {componente}",
                    campo_salida,
                    "DINAMICO",
                    "CAMPO_FLUJO",
                    f"El campo {campo_salida} es producido dinámicamente por {componente}.",
                    contexto_xml="row-meta",
                    confianza="alta",
                )
        elif tipo_normalizado in {"getvariable", "getvariables"}:
            cantidad_anterior = len(transiciones)
            for campo in _iterar_campos_configuracion(elemento):
                destino = obtener_texto_hijo_directo(campo, ("name", "field_name"))
                expresion = obtener_texto_hijo_directo(
                    campo, ("variable", "variable_value", "value")
                )
                fuentes = extraer_nombres_referenciados(expresion)
                if not fuentes and expresion.strip():
                    fuentes = [expresion.strip()]
                for fuente in fuentes:
                    if fuente in parametros_declarados:
                        agregar(
                            "PARAMETRO_USADO_COMO_VARIABLE",
                            componente,
                            tipo_componente,
                            fuente,
                            fuente,
                            "PARAMETRO",
                            "VARIABLE",
                            (
                                f"El parámetro contextual {fuente} se consulta "
                                "explícitamente mediante GetVariable."
                            ),
                            valor=expresion,
                            contexto_xml="field",
                            confianza="alta",
                        )
                    agregar(
                        "GET_VARIABLE",
                        componente,
                        tipo_componente,
                        fuente,
                        destino,
                        "VARIABLE",
                        "CAMPO_FLUJO",
                        f"{expresion or fuente} se lee en el campo {destino}.",
                        valor=expresion,
                        contexto_xml="field",
                    )
            if len(transiciones) > cantidad_anterior:
                componentes_semanticos.add((componente, tipo_componente))
        elif tipo_normalizado in {"setvariable", "setvariables"}:
            cantidad_anterior = len(transiciones)
            for campo in _iterar_campos_configuracion(elemento):
                origen = obtener_texto_hijo_directo(
                    campo, ("field_name", "stream_name", "name")
                )
                destino = obtener_texto_hijo_directo(
                    campo, ("variable_name", "parameter_name")
                )
                alcance = obtener_texto_hijo_directo(
                    campo, ("variable_type", "scope", "variable_scope")
                )
                valor_default = obtener_texto_hijo_directo(
                    campo, ("default_value", "default")
                )
                detalle = f"El campo {origen} crea o actualiza la variable {destino}."
                if alcance:
                    detalle += f" Alcance: {alcance}."
                if valor_default:
                    detalle += f" Valor por defecto: {valor_default}."
                agregar(
                    "SET_VARIABLE",
                    componente,
                    tipo_componente,
                    origen,
                    destino,
                    "CAMPO_FLUJO",
                    "VARIABLE",
                    detalle,
                    alcance=alcance,
                    valor_default=valor_default,
                    contexto_xml="field",
                )
            if len(transiciones) > cantidad_anterior:
                componentes_semanticos.add((componente, tipo_componente))

    # Conserva la detección genérica anterior para parámetros, fórmulas y
    # asignaciones explícitas que no pertenecen a Get/Set Variable.
    for asignacion in extraer_asignaciones_estaticas(objeto):
        if (
            asignacion.get("componente", ""),
            asignacion.get("tipo_componente", ""),
        ) in componentes_semanticos:
            continue
        destino = asignacion.get("variable_destino", "")
        valor = asignacion.get("valor", "")
        referencias = asignacion.get("referencias", [])
        if referencias:
            for fuente in referencias:
                origen_tipo = (
                    "PARAMETRO" if fuente in parametros_declarados else "VARIABLE"
                )
                if origen_tipo == "PARAMETRO":
                    tipo_evento = "PARAMETRO_ASIGNA_VARIABLE"
                else:
                    tipo_evento = "RENOMBRE" if valor.strip() in {
                        f"${{{fuente}}}", f"%%{fuente}%%"
                    } else "DERIVACION"
                agregar(
                    tipo_evento,
                    asignacion.get("componente", ""),
                    asignacion.get("tipo_componente", ""),
                    fuente,
                    destino,
                    origen_tipo,
                    "VARIABLE",
                    f"Expresión/valor: {valor}",
                    valor=valor,
                    contexto_xml=asignacion.get("contexto_xml", ""),
                )
        elif valor or asignacion.get("tiene_valor_configurado", False):
            agregar(
                "ASIGNACION_INTERNA",
                asignacion.get("componente", ""),
                asignacion.get("tipo_componente", ""),
                valor,
                destino,
                "LITERAL",
                "VARIABLE",
                f"Valor interno: {valor if valor else '(vacío)'}",
                valor=valor,
                contexto_xml=asignacion.get("contexto_xml", ""),
            )
    return transiciones


def _hay_ruta_habilitada(
    objeto: dict[str, Any], origen: str, destino: str
) -> bool:
    if origen == destino:
        return True
    sucesores: dict[str, set[str]] = {}
    for hop in extraer_hops_transformacion(objeto["xml_element"]):
        if es_falso(hop.get("habilitado", "Y")):
            continue
        sucesores.setdefault(hop.get("origen", ""), set()).add(hop.get("destino", ""))
    visitados: set[str] = set()
    pendientes = [origen]
    while pendientes:
        actual = pendientes.pop()
        if actual in visitados:
            continue
        visitados.add(actual)
        for siguiente in sucesores.get(actual, set()):
            if siguiente == destino:
                return True
            if siguiente not in visitados:
                pendientes.append(siguiente)
    return False


def construir_modelo_linaje(
    resultado: dict[str, Any],
    inventario: dict[str, Any],
    propiedades_kettle: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Construye un grafo tipado sin fusionar variables y parámetros."""
    indice_kettle = crear_indice_kettle_normalizado(propiedades_kettle or {})
    transiciones: list[dict[str, Any]] = []
    objetos: dict[str, dict[str, Any]] = {}
    for proceso_id, nodo in resultado.get("nodos", {}).items():
        objeto = obtener_objeto_inventario(nodo["tipo"], proceso_id, inventario)
        if not objeto:
            continue
        objetos[proceso_id] = objeto
        transiciones.extend(extraer_transiciones_linaje(objeto))

    informacion = resultado.get("parametros") or crear_informacion_parametros_vacia()
    parametros_declarados: dict[tuple[str, str], dict[str, Any]] = {}
    parametros_por_nombre: dict[str, list[dict[str, Any]]] = {}
    parametros_por_proceso: dict[str, set[str]] = {}
    for proceso in informacion.get("procesos", []):
        proceso_id = proceso.get("id", "")
        for parametro in proceso.get("parametros_declarados", []):
            nombre = normalizar_clave_kettle(parametro.get("nombre", ""))
            if not nombre:
                continue
            declaracion = {
                "nombre": nombre,
                "valor_default": parametro.get("valor_default", ""),
                "tiene_valor_default": parametro.get(
                    "tiene_valor_default", bool(parametro.get("valor_default", ""))
                ),
                "descripcion": parametro.get("descripcion", ""),
                "proceso_id": proceso_id,
                "proceso": proceso.get("nombre", ""),
                "tipo_proceso": proceso.get("tipo", ""),
                "ruta": proceso.get("ruta", ""),
                "componente": "parameters",
            }
            parametros_declarados[(proceso_id, nombre)] = declaracion
            parametros_por_proceso.setdefault(proceso_id, set()).add(nombre)
            parametros_por_nombre.setdefault(nombre, []).append(declaracion)

    variables_por_proceso: dict[str, set[str]] = {}
    for transicion in transiciones:
        proceso_id = transicion.get("proceso_id", "")
        if transicion.get("origen_tipo") == "VARIABLE":
            variables_por_proceso.setdefault(proceso_id, set()).add(
                normalizar_clave_kettle(transicion.get("variable_origen", ""))
            )
        if transicion.get("destino_tipo") == "VARIABLE":
            variables_por_proceso.setdefault(proceso_id, set()).add(
                normalizar_clave_kettle(transicion.get("variable_destino", ""))
            )

    informacion_variables = (
        resultado.get("variables") or crear_informacion_variables_vacia()
    )
    for proceso in informacion_variables.get("procesos", []):
        proceso_id = proceso.get("id", "")
        for referencia in proceso.get("referencias", []):
            if referencia.get("clasificacion") in {"VARIABLE", "VARIABLE_KETTLE"}:
                nombre = normalizar_clave_kettle(referencia.get("nombre", ""))
                if nombre:
                    variables_por_proceso.setdefault(proceso_id, set()).add(nombre)

    parametros_destino_llamadas: dict[tuple[str, str], dict[str, Any]] = {}
    for asignacion in [
        *informacion.get("asignaciones_entre_procesos", []),
        *informacion.get("asignaciones_desde_scripts", []),
    ]:
        nombre = normalizar_clave_kettle(
            asignacion.get("parametro_destino", "")
        )
        proceso_id = asignacion.get("proceso_destino_id", "")
        if nombre:
            parametros_destino_llamadas.setdefault(
                (proceso_id, nombre), asignacion
            )

    for asignacion in informacion.get("asignaciones_entre_procesos", []):
        destino = normalizar_clave_kettle(asignacion.get("parametro_destino", ""))
        if not destino:
            continue
        proceso_origen_id = asignacion.get("proceso_origen_id", "")
        proceso_destino_id = asignacion.get("proceso_destino_id", "")
        valor = asignacion.get("valor_configurado", "")
        origenes = asignacion.get("origenes")
        if origenes is None:
            origenes = clasificar_valor_asignado_parametro(
                valor, parametros_por_proceso.get(proceso_origen_id, set())
            )["origenes"]
        if not origenes:
            origenes = [{"tipo": "LITERAL", "nombre": valor}]
        for origen in origenes:
            origen_tipo = origen.get("tipo", "DESCONOCIDO")
            fuente = origen.get("nombre", "")
            tipo_evento = {
                "VARIABLE": "VARIABLE_ASIGNA_PARAMETRO",
                "PARAMETRO": "PARAMETRO_ASIGNA_PARAMETRO",
                "LITERAL": "LITERAL_ASIGNA_PARAMETRO",
                "CAMPO_FLUJO": "CAMPO_FLUJO_ASIGNA_PARAMETRO",
            }.get(origen_tipo, "ORIGEN_PARAMETRO_NO_RESUELTO")
            transiciones.append(
                {
                    "tipo_evento": tipo_evento,
                    "proceso_id": proceso_origen_id,
                    "proceso": asignacion.get("proceso_origen", ""),
                    "tipo_proceso": asignacion.get("tipo_origen", ""),
                    "ruta": asignacion.get("ruta_origen", ""),
                    "componente": asignacion.get("entrada", ""),
                    "tipo_componente": asignacion.get("tipo_entrada", "ENTRY"),
                    "variable_origen": fuente,
                    "variable_destino": destino,
                    "origen_tipo": origen_tipo,
                    "destino_tipo": "PARAMETRO",
                    "origen_proceso_id": proceso_origen_id,
                    "destino_proceso_id": proceso_destino_id,
                    "proceso_destino": asignacion.get("proceso_destino", ""),
                    "tipo_proceso_destino": asignacion.get("tipo_destino", ""),
                    "ruta_destino": asignacion.get("ruta_destino", ""),
                    "parametro_destino": destino,
                    "valor_configurado": valor,
                    "contexto_xml": asignacion.get("contexto_xml", "parameter"),
                    "detalle": (
                        f"{asignacion.get('proceso_origen', '')} envía {valor!r} a "
                        f"{asignacion.get('proceso_destino', '')}.{destino}."
                    ),
                    "estado": asignacion.get("estado", "resuelto"),
                    "confianza": asignacion.get("confianza", "alta"),
                }
            )

    for asignacion in informacion.get("asignaciones_desde_scripts", []):
        destino = normalizar_clave_kettle(
            asignacion.get("parametro_destino", "")
        )
        proceso_destino_id = asignacion.get("proceso_destino_id", "")
        if not destino or proceso_destino_id not in resultado.get("nodos", {}):
            continue
        origen_tipo = asignacion.get("origen_tipo", "LITERAL_SCRIPT")
        transiciones.append(
            {
                "tipo_evento": "SCRIPT_ASIGNA_PARAMETRO",
                "proceso_id": asignacion.get("proceso_origen_id", ""),
                "proceso": asignacion.get("proceso_origen", ""),
                "tipo_proceso": "SCRIPT_SHELL",
                "ruta": asignacion.get("ruta_script", ""),
                "componente": "kitchen.sh -param",
                "tipo_componente": "SCRIPT_SHELL",
                "variable_origen": asignacion.get("origen", ""),
                "variable_destino": destino,
                "origen_tipo": origen_tipo,
                "destino_tipo": "PARAMETRO",
                "origen_proceso_id": asignacion.get("proceso_origen_id", ""),
                "destino_proceso_id": proceso_destino_id,
                "proceso_destino": asignacion.get("proceso_destino", ""),
                "tipo_proceso_destino": asignacion.get("tipo_destino", "JOB"),
                "ruta_destino": asignacion.get("ruta_destino", ""),
                "parametro_destino": destino,
                "valor_configurado": asignacion.get("valor_configurado", ""),
                "variables_shell": asignacion.get("variables_shell", []),
                "variables_intermedias": asignacion.get(
                    "variables_intermedias", []
                ),
                "script": asignacion.get("script", ""),
                "linea": asignacion.get("linea"),
                "contexto_xml": "kitchen.sh -param",
                "detalle": (
                    f"{asignacion.get('script', '')} asigna "
                    f"{asignacion.get('origen', '')!r} a "
                    f"{asignacion.get('proceso_destino', '')}.{destino}."
                ),
                "estado": asignacion.get("estado", "resuelto"),
                "confianza": asignacion.get("confianza", "alta"),
            }
        )

    for (proceso_id, nombre), declaracion in parametros_declarados.items():
        valor = declaracion.get("valor_default", "")
        if not declaracion.get("tiene_valor_default", bool(valor)):
            continue
        referencias = extraer_nombres_referenciados(valor)
        if not referencias:
            fuentes = [("LITERAL", valor, "VALOR_PREDETERMINADO")]
        else:
            fuentes = []
            for fuente in referencias:
                if fuente in parametros_por_proceso.get(proceso_id, set()):
                    origen_tipo = "PARAMETRO"
                    evento = "PARAMETRO_ASIGNA_PARAMETRO"
                elif fuente in variables_por_proceso.get(proceso_id, set()):
                    origen_tipo = "VARIABLE"
                    evento = "VARIABLE_ASIGNA_PARAMETRO"
                elif buscar_variable_en_kettle(fuente, indice_kettle) is not None:
                    origen_tipo = "VARIABLE"
                    evento = "VARIABLE_ASIGNA_PARAMETRO"
                else:
                    origen_tipo = "DESCONOCIDO"
                    evento = "ORIGEN_PARAMETRO_NO_RESUELTO"
                fuentes.append((origen_tipo, fuente, evento))
        for origen_tipo, fuente, tipo_evento in fuentes:
            transiciones.append(
                {
                    "tipo_evento": tipo_evento,
                    "proceso_id": proceso_id,
                    "proceso": declaracion.get("proceso", ""),
                    "tipo_proceso": declaracion.get("tipo_proceso", ""),
                    "ruta": declaracion.get("ruta", ""),
                    "componente": "parameters",
                    "tipo_componente": "PARAMETER",
                    "variable_origen": fuente,
                    "variable_destino": nombre,
                    "origen_tipo": origen_tipo,
                    "destino_tipo": "PARAMETRO",
                    "origen_proceso_id": proceso_id,
                    "destino_proceso_id": proceso_id,
                    "parametro_destino": nombre,
                    "valor_configurado": valor,
                    "contexto_xml": "default_value",
                    "detalle": f"Valor por defecto: {valor}",
                    "estado": "no_resuelto" if origen_tipo == "DESCONOCIDO" else "resuelto",
                    "confianza": "alta" if origen_tipo != "DESCONOCIDO" else "baja",
                }
            )

    for transicion in transiciones:
        if transicion.get("origen_tipo") in {"VARIABLE", "PARAMETRO"}:
            transicion["variable_origen"] = normalizar_clave_kettle(
                transicion.get("variable_origen", "")
            )
        else:
            transicion["variable_origen"] = str(
                transicion.get("variable_origen", "")
            ).strip()
        if transicion.get("destino_tipo") in {"VARIABLE", "PARAMETRO"}:
            transicion["variable_destino"] = normalizar_clave_kettle(
                transicion.get("variable_destino", "")
            )
        else:
            transicion["variable_destino"] = str(
                transicion.get("variable_destino", "")
            ).strip()

    nodos: dict[tuple[str, ...], dict[str, Any]] = {}
    aristas: list[dict[str, Any]] = []
    productores_campos: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def nodo_variable(nombre: str) -> tuple[str, ...]:
        nombre = normalizar_clave_kettle(nombre)
        node_id = ("VARIABLE", nombre)
        nodos.setdefault(node_id, {"id": node_id, "tipo": "VARIABLE", "nombre": nombre})
        return node_id

    def nodo_parametro(proceso_id: str, nombre: str) -> tuple[str, ...]:
        nombre = normalizar_clave_kettle(nombre)
        node_id = ("PARAMETRO", proceso_id, nombre)
        declaracion = parametros_declarados.get((proceso_id, nombre), {})
        asignacion = parametros_destino_llamadas.get((proceso_id, nombre), {})
        objeto = objetos.get(proceso_id, {})
        nodos.setdefault(
            node_id,
            {
                "id": node_id,
                "tipo": "PARAMETRO",
                "nombre": nombre,
                "proceso_id": proceso_id,
                "proceso": declaracion.get(
                    "proceso",
                    objeto.get("nombre", asignacion.get("proceso_destino", "")),
                ),
                "tipo_proceso": declaracion.get(
                    "tipo_proceso",
                    objeto.get("tipo", asignacion.get("tipo_destino", "")),
                ),
                "ruta": declaracion.get(
                    "ruta", objeto.get("ruta", asignacion.get("ruta_destino", ""))
                ),
                "declarado": bool(declaracion),
                "valor_default": declaracion.get("valor_default", ""),
                "tiene_valor_default": declaracion.get(
                    "tiene_valor_default", False
                ),
                "descripcion": declaracion.get("descripcion", ""),
            },
        )
        return node_id

    def nodo_campo(
        proceso_id: str, componente: str, nombre: str
    ) -> tuple[str, ...]:
        nombre = (nombre or "").strip()
        node_id = ("CAMPO_FLUJO", proceso_id, componente, nombre)
        nodos.setdefault(
            node_id,
            {
                "id": node_id,
                "tipo": "CAMPO_FLUJO",
                "nombre": nombre,
                "proceso_id": proceso_id,
                "componente": componente,
            },
        )
        return node_id

    def nodo_terminal(tipo: str, transicion: dict[str, Any], indice: int) -> tuple[str, ...]:
        node_id = (tipo, str(indice))
        nodos.setdefault(
            node_id,
            {
                "id": node_id,
                "tipo": tipo,
                "nombre": transicion.get("variable_origen", ""),
                "proceso": transicion.get("proceso", ""),
                "componente": transicion.get("componente", ""),
            },
        )
        return node_id

    def agregar_arista(
        origen_id: tuple[str, ...],
        destino_id: tuple[str, ...],
        transicion: dict[str, Any],
        *,
        estado: str | None = None,
    ) -> None:
        aristas.append(
            {
                **transicion,
                "id": f"arista_{len(aristas) + 1}",
                "indice": len(aristas),
                "origen_id": origen_id,
                "destino_id": destino_id,
                "estado": estado or transicion.get("estado", "resuelto"),
            }
        )

    for proceso_id, nombre in parametros_declarados:
        nodo_parametro(proceso_id, nombre)

    # Primero se registran productores GetVariable y resultados dinámicos.
    for indice, transicion in enumerate(transiciones):
        tipo_evento = transicion.get("tipo_evento")
        if tipo_evento not in {"GET_VARIABLE", "RESULTADO_SQL", "ORIGEN_DINAMICO"}:
            continue
        if tipo_evento == "GET_VARIABLE":
            origen_id = nodo_variable(transicion["variable_origen"])
        else:
            origen_id = nodo_terminal("DINAMICO", transicion, indice)
        destino_id = nodo_campo(
            transicion.get("proceso_id", ""),
            transicion.get("componente", ""),
            transicion["variable_destino"],
        )
        agregar_arista(origen_id, destino_id, transicion)
        productores_campos.setdefault(
            (transicion.get("proceso_id", ""), transicion["variable_destino"]), []
        ).append(
            {
                "nodo_id": destino_id,
                "componente": transicion.get("componente", ""),
            }
        )

    for indice, transicion in enumerate(transiciones):
        tipo_evento = transicion.get("tipo_evento", "")
        if tipo_evento in {"GET_VARIABLE", "RESULTADO_SQL", "ORIGEN_DINAMICO"}:
            continue
        destino_tipo = transicion.get("destino_tipo", "VARIABLE")
        if destino_tipo == "PARAMETRO":
            destino_id = nodo_parametro(
                transicion.get("destino_proceso_id", transicion.get("proceso_id", "")),
                transicion.get("variable_destino", ""),
            )
        else:
            destino_id = nodo_variable(transicion.get("variable_destino", ""))
        origen_tipo = transicion.get("origen_tipo", "VARIABLE")
        if tipo_evento == "SET_VARIABLE":
            proceso_id = transicion.get("proceso_id", "")
            objeto = objetos.get(proceso_id)
            candidatos = []
            for productor in productores_campos.get(
                (proceso_id, transicion.get("variable_origen", "")), []
            ):
                if objeto and _hay_ruta_habilitada(
                    objeto,
                    productor["componente"],
                    transicion.get("componente", ""),
                ):
                    candidatos.append(productor)
            if candidatos:
                for productor in candidatos:
                    agregar_arista(
                        productor["nodo_id"], destino_id, transicion, estado="resuelto"
                    )
            else:
                origen_id = nodo_campo(
                    proceso_id, "[productor no identificado]", transicion.get("variable_origen", "")
                )
                agregar_arista(origen_id, destino_id, transicion, estado="no_resuelto")
        elif origen_tipo == "LITERAL":
            agregar_arista(nodo_terminal("LITERAL", transicion, indice), destino_id, transicion)
        elif origen_tipo == "DESCONOCIDO":
            agregar_arista(nodo_terminal("DESCONOCIDO", transicion, indice), destino_id, transicion)
        elif origen_tipo == "DINAMICO":
            agregar_arista(nodo_terminal("DINAMICO", transicion, indice), destino_id, transicion)
        elif origen_tipo in {
            "VARIABLE_SHELL",
            "EXPRESION_SHELL",
            "LITERAL_SCRIPT",
        }:
            agregar_arista(
                nodo_terminal(origen_tipo, transicion, indice),
                destino_id,
                transicion,
            )
        elif origen_tipo == "PARAMETRO":
            agregar_arista(
                nodo_parametro(
                    transicion.get(
                        "origen_proceso_id", transicion.get("proceso_id", "")
                    ),
                    transicion.get("variable_origen", ""),
                ),
                destino_id,
                transicion,
            )
        elif origen_tipo == "CAMPO_FLUJO":
            agregar_arista(
                nodo_campo(
                    transicion.get(
                        "origen_proceso_id", transicion.get("proceso_id", "")
                    ),
                    transicion.get("componente", ""),
                    transicion.get("variable_origen", ""),
                ),
                destino_id,
                transicion,
            )
        else:
            agregar_arista(
                nodo_variable(transicion.get("variable_origen", "")),
                destino_id,
                transicion,
            )

    entrantes: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    salientes: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    creaciones: dict[str, list[dict[str, Any]]] = {}
    for arista in aristas:
        entrantes.setdefault(arista["destino_id"], []).append(arista)
        salientes.setdefault(arista["origen_id"], []).append(arista)
        destino = arista.get("variable_destino", "")
        if arista.get("destino_tipo") != "VARIABLE" or not destino:
            continue
        if arista.get("origen_tipo") == "VARIABLE" and arista.get("variable_origen") == destino:
            continue
        resumen = {
            "tipo_evento": arista.get("tipo_evento", ""),
            "proceso": arista.get("proceso", ""),
            "componente": arista.get("componente", ""),
            "variable_origen": arista.get("variable_origen", ""),
            "variable_destino": destino,
            "estado": arista.get("estado", ""),
            "alcance": arista.get("alcance", ""),
        }
        if resumen not in creaciones.setdefault(destino, []):
            creaciones[destino].append(resumen)

    return {
        "nodos": nodos,
        "aristas": aristas,
        "entrantes": entrantes,
        "salientes": salientes,
        "creaciones": creaciones,
        "parametros_declarados": parametros_declarados,
        "parametros_por_nombre": parametros_por_nombre,
    }


def _resolver_dependencias_modelo(
    modelo: dict[str, Any],
    variable: str,
    indice_kettle_normalizado: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    variable = normalizar_clave_kettle(variable)
    inicio = ("VARIABLE", variable)
    nodos_aguas_arriba: set[tuple[str, ...]] = {inicio}
    aristas_aguas_arriba: dict[str, dict[str, Any]] = {}
    pendientes = [inicio]
    while pendientes:
        nodo = pendientes.pop()
        for arista in modelo["entrantes"].get(nodo, []):
            if arista["id"] in aristas_aguas_arriba:
                continue
            aristas_aguas_arriba[arista["id"]] = arista
            origen = arista["origen_id"]
            if origen not in nodos_aguas_arriba:
                nodos_aguas_arriba.add(origen)
                pendientes.append(origen)

    nodos_raiz = {
        nodo for nodo in nodos_aguas_arriba if not modelo["entrantes"].get(nodo)
    }
    fuentes_kettle = sorted(
        {
            datos["nombre"]
            for nodo in nodos_raiz
            if nodo and nodo[0] == "VARIABLE" and len(nodo) > 1
            if (datos := buscar_variable_en_kettle(nodo[1], indice_kettle_normalizado))
            is not None
        },
        key=lambda nombre: (nombre.lower(), nombre),
    )
    origenes_internos = [
        modelo["nodos"].get(nodo, {"tipo": "LITERAL", "nombre": nodo[-1]})
        for nodo in nodos_raiz
        if nodo and nodo[0] == "LITERAL"
    ]
    origenes_dinamicos = [
        modelo["nodos"].get(nodo, {"tipo": "DINAMICO", "nombre": nodo[-1]})
        for nodo in nodos_raiz
        if nodo and nodo[0] == "DINAMICO"
    ]
    origenes_parametros: list[dict[str, Any]] = []
    raices_no_resueltas: list[dict[str, Any]] = []
    for nodo in nodos_raiz:
        datos = modelo["nodos"].get(
            nodo, {"id": nodo, "tipo": nodo[0], "nombre": nodo[-1] if nodo else variable}
        )
        if datos.get("tipo") in {"LITERAL", "DINAMICO"}:
            continue
        if datos.get("tipo") == "PARAMETRO":
            if datos not in origenes_parametros:
                origenes_parametros.append(datos)
            continue
        if datos.get("tipo") == "VARIABLE":
            nombre_raiz = normalizar_clave_kettle(datos.get("nombre", ""))
            if buscar_variable_en_kettle(
                nombre_raiz, indice_kettle_normalizado
            ) is not None:
                continue
        raices_no_resueltas.append(datos)
    if (
        not fuentes_kettle
        and not origenes_internos
        and not origenes_dinamicos
        and not origenes_parametros
        and not raices_no_resueltas
    ):
        raices_no_resueltas.append(
            modelo["nodos"].get(
                inicio, {"id": inicio, "tipo": "VARIABLE", "nombre": variable}
            )
        )

    campos = sorted(
        {
            modelo["nodos"].get(nodo, {}).get("nombre", "")
            for nodo in nodos_aguas_arriba
            if nodo and nodo[0] == "CAMPO_FLUJO"
        }
        - {""},
        key=lambda nombre: (nombre.lower(), nombre),
    )
    return {
        "inicio": inicio,
        "nodos_aguas_arriba": nodos_aguas_arriba,
        "aristas_aguas_arriba": aristas_aguas_arriba,
        "fuentes_kettle": fuentes_kettle,
        "origenes_internos": origenes_internos,
        "origenes_dinamicos": origenes_dinamicos,
        "origenes_parametros": origenes_parametros,
        "raices_no_resueltas": raices_no_resueltas,
        "campos_intermedios": campos,
    }


def _enumerar_rutas_origen_modelo(
    modelo: dict[str, Any],
    variable: str,
    indice_kettle_normalizado: dict[str, dict[str, Any]],
    limite: int = 200,
) -> list[dict[str, Any]]:
    """Enumera ramas causales independientes desde cada raíz hasta la variable."""
    inicio = ("VARIABLE", normalizar_clave_kettle(variable))
    rutas: list[dict[str, Any]] = []

    def finalizar(
        tipo_origen: str,
        nodo: tuple[str, ...],
        aristas_inversas: list[dict[str, Any]],
        datos_origen: dict[str, Any],
    ) -> None:
        if len(rutas) >= limite:
            return
        aristas = list(reversed(aristas_inversas))
        nodos = [nodo]
        nodos.extend(arista["destino_id"] for arista in aristas)
        rutas.append(
            {
                "tipo_origen": tipo_origen,
                "nodo_origen": nodo,
                "datos_origen": datos_origen,
                "aristas": aristas,
                "nodos": nodos,
            }
        )

    def recorrer(
        nodo: tuple[str, ...],
        aristas_inversas: list[dict[str, Any]],
        visitados: set[tuple[str, ...]],
    ) -> None:
        if len(rutas) >= limite:
            return
        datos_nodo = modelo["nodos"].get(
            nodo,
            {"id": nodo, "tipo": nodo[0], "nombre": nodo[-1] if nodo else variable},
        )
        if nodo and nodo[0] == "DINAMICO":
            finalizar("dinamico", nodo, aristas_inversas, datos_nodo)
            return
        if nodo and nodo[0] == "LITERAL":
            finalizar("interno", nodo, aristas_inversas, datos_nodo)
            return

        entrantes = modelo["entrantes"].get(nodo, [])
        if not entrantes:
            if nodo and nodo[0] == "PARAMETRO":
                finalizar("parametro", nodo, aristas_inversas, datos_nodo)
                return
            if nodo and nodo[0] == "VARIABLE":
                propiedad = buscar_variable_en_kettle(
                    nodo[1], indice_kettle_normalizado
                )
                if propiedad is not None:
                    finalizar("kettle", nodo, aristas_inversas, propiedad)
                    return
            finalizar("no_resuelto", nodo, aristas_inversas, datos_nodo)
            return
        recorrio = False
        for arista in entrantes:
            origen = arista["origen_id"]
            if origen in visitados:
                continue
            recorrio = True
            recorrer(origen, [*aristas_inversas, arista], {*visitados, origen})
        if not recorrio:
            finalizar("no_resuelto", nodo, aristas_inversas, datos_nodo)

    recorrer(inicio, [], {inicio})
    if not rutas:
        finalizar(
            "no_resuelto",
            inicio,
            [],
            modelo["nodos"].get(
                inicio, {"id": inicio, "tipo": "VARIABLE", "nombre": variable}
            ),
        )

    firmas: set[tuple[Any, ...]] = set()
    unicas: list[dict[str, Any]] = []
    for ruta in rutas:
        firma = (
            ruta["tipo_origen"],
            ruta["datos_origen"].get("nombre", ""),
            ruta["datos_origen"].get("proceso_id", ""),
            ruta["datos_origen"].get("componente", ""),
            *(arista["id"] for arista in ruta["aristas"]),
        )
        if firma in firmas:
            continue
        firmas.add(firma)
        unicas.append(ruta)

    usados: set[str] = set()
    contador_generico = 0
    for ruta in unicas:
        nombre = normalizar_clave_kettle(ruta["datos_origen"].get("nombre", ""))
        nombre_minusculas = nombre.lower()
        if "histor" in nombre_minusculas:
            base = "historica"
        elif nombre_minusculas == "tabla_transacciones" or (
            "transacciones" in nombre_minusculas and "histor" not in nombre_minusculas
        ):
            base = "normal"
        else:
            contador_generico += 1
            base = f"ruta_{contador_generico}"
        identificador = base
        sufijo = 2
        while identificador in usados:
            identificador = f"{base}_{sufijo}"
            sufijo += 1
        usados.add(identificador)
        ruta["ruta_origen"] = identificador
    return unicas


def _construir_linaje_variable_lineal(
    variable: str,
    resultado: dict[str, Any],
    inventario: dict[str, Any],
    propiedades_kettle: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reconstruye el origen antes de decidir si una variable está resuelta."""
    propiedades_kettle = propiedades_kettle or {}
    variable = normalizar_clave_kettle(variable)
    indice_kettle = crear_indice_kettle_normalizado(propiedades_kettle)
    modelo = construir_modelo_linaje(resultado, inventario, propiedades_kettle)
    resolucion = _resolver_dependencias_modelo(modelo, variable, indice_kettle)
    eventos: list[dict[str, Any]] = []
    no_resueltos: list[str] = []

    def agregar(
        tipo: str,
        proceso: str,
        componente: str,
        origen: str,
        destino: str,
        detalle: str,
        estado: str = "resuelto",
        **extras: Any,
    ) -> None:
        eventos.append(
            {
                "orden": len(eventos) + 1,
                "tipo_evento": tipo,
                "proceso": proceso,
                "componente": componente,
                "variable_origen": origen,
                "variable_destino": destino,
                "detalle": detalle,
                "estado": estado,
                **extras,
            }
        )

    # Además de las dependencias hacia atrás, conserva derivaciones y usos
    # posteriores cuando se selecciona la variable fuente de la cadena.
    nodos_relevantes = set(resolucion["nodos_aguas_arriba"])
    aristas_relevantes = dict(resolucion["aristas_aguas_arriba"])
    pendientes = [resolucion["inicio"]]
    visitados_adelante: set[tuple[str, ...]] = set()
    while pendientes:
        nodo = pendientes.pop()
        if nodo in visitados_adelante:
            continue
        visitados_adelante.add(nodo)
        nodos_relevantes.add(nodo)
        for arista in modelo["salientes"].get(nodo, []):
            aristas_relevantes[arista["id"]] = arista
            destino = arista["destino_id"]
            nodos_relevantes.add(destino)
            if destino not in visitados_adelante:
                pendientes.append(destino)

    origenes_kettle: list[dict[str, Any]] = []
    raices_eventos: set[tuple[str, ...]] = set()
    for nombre in resolucion["fuentes_kettle"]:
        datos_propiedad = buscar_variable_en_kettle(nombre, indice_kettle)
        if datos_propiedad is None:
            continue
        propiedad = _datos_kettle_seguros(datos_propiedad)
        origenes_kettle.append(propiedad)
        raices_eventos.add(("VARIABLE", nombre))
        agregar(
            "ORIGEN_KETTLE",
            propiedad.get("archivo", "kettle.properties"),
            f"línea {propiedad.get('linea', '')}",
            nombre,
            nombre,
            f"Valor: {propiedad.get('valor_enmascarado', '[valor oculto]')}",
            confianza="alta",
            nodo_origen=("VARIABLE", nombre),
            nodo_destino=("VARIABLE", nombre),
        )

    for origen_interno in resolucion["origenes_internos"]:
        node_id = origen_interno.get("id")
        if node_id:
            raices_eventos.add(node_id)
        agregar(
            "ORIGEN_INTERNO",
            origen_interno.get("proceso", "-"),
            origen_interno.get("componente", "-"),
            origen_interno.get("nombre", ""),
            variable,
            "El valor se define dentro del proceso y no procede de kettle.properties.",
            nodo_origen=node_id,
            nodo_destino=resolucion["inicio"],
        )

    for raiz in resolucion["raices_no_resueltas"]:
        node_id = raiz.get("id")
        if node_id:
            raices_eventos.add(node_id)
        if raiz.get("tipo") == "CAMPO_FLUJO":
            mensaje = (
                f"No se identificó un productor alcanzable para el campo "
                f"{raiz.get('nombre', '')}."
            )
        else:
            mensaje = (
                f"No existe coincidencia directa ni una derivación estática resuelta "
                f"para {raiz.get('nombre', variable)}."
            )
        no_resueltos.append(mensaje)
        agregar(
            "ORIGEN_NO_RESUELTO",
            raiz.get("proceso", "-"),
            raiz.get("componente", "-"),
            raiz.get("nombre", variable),
            variable,
            mensaje,
            "no_resuelto",
            nodo_origen=node_id,
            nodo_destino=resolucion["inicio"],
        )

    if not raices_eventos:
        raices_eventos.add(resolucion["inicio"])
    distancias = {nodo: 0 for nodo in raices_eventos}
    cola = deque(raices_eventos)
    while cola:
        nodo = cola.popleft()
        for arista in modelo["salientes"].get(nodo, []):
            if arista["id"] not in aristas_relevantes:
                continue
            destino = arista["destino_id"]
            distancia = distancias[nodo] + 1
            if destino not in distancias or distancia < distancias[destino]:
                distancias[destino] = distancia
                cola.append(destino)

    aristas_ordenadas = sorted(
        aristas_relevantes.values(),
        key=lambda item: (
            distancias.get(item["origen_id"], 10**6),
            distancias.get(item["destino_id"], 10**6),
            item.get("indice", 0),
        ),
    )
    referencias_cubiertas: set[tuple[str, str, str]] = set()
    for arista in aristas_ordenadas:
        if arista.get("estado") == "ambiguo":
            no_resueltos.append(
                f"{arista.get('proceso', '')} / {arista.get('componente', '')}: "
                f"el campo {arista.get('variable_origen', '')} tiene varios productores "
                "alcanzables; se muestran todos los orígenes posibles."
            )
        agregar(
            arista.get("tipo_evento", "DERIVACION"),
            arista.get("proceso", ""),
            arista.get("componente", ""),
            arista.get("variable_origen", ""),
            arista.get("variable_destino", ""),
            arista.get("detalle", ""),
            arista.get("estado", "resuelto"),
            nodo_origen=arista.get("origen_id"),
            nodo_destino=arista.get("destino_id"),
            alcance=arista.get("alcance", ""),
            origen_tipo=arista.get("origen_tipo", ""),
            destino_tipo=arista.get("destino_tipo", ""),
            proceso_id=arista.get("proceso_id", ""),
            proceso_destino_id=arista.get("destino_proceso_id", ""),
            proceso_destino=arista.get("proceso_destino", ""),
            contexto=arista.get("contexto_xml", ""),
            valor_configurado=arista.get("valor_configurado", ""),
        )
        if arista.get("origen_tipo") == "VARIABLE":
            referencias_cubiertas.add(
                (
                    arista.get("proceso_id", ""),
                    arista.get("componente", ""),
                    arista.get("variable_origen", ""),
                )
            )

    aliases = sorted(
        {
            nodo[1]
            for nodo in nodos_relevantes
            if nodo and nodo[0] == "VARIABLE" and len(nodo) > 1 and nodo[1]
        },
        key=lambda nombre: (nombre.lower(), nombre),
    )
    campos_flujo = sorted(
        {
            modelo["nodos"].get(nodo, {}).get("nombre", "")
            for nodo in nodos_relevantes
            if nodo and nodo[0] == "CAMPO_FLUJO"
        }
        - {""},
        key=lambda nombre: (nombre.lower(), nombre),
    )

    for alias in aliases:
        localizacion = localizar_variable(
            alias, resultado, inventario, propiedades_kettle
        )
        for aparicion in localizacion.get("apariciones", []):
            if aparicion.get("tipo_aparicion") not in {
                "REFERENCIA_VARIABLE",
                "REFERENCIA_VARIABLE_KETTLE",
            }:
                continue
            clave_cubierta = (
                aparicion.get("proceso_id", ""),
                aparicion.get("componente") or "",
                alias,
            )
            if clave_cubierta in referencias_cubiertas:
                continue
            agregar(
                "USO",
                aparicion["proceso"],
                aparicion.get("componente") or aparicion.get("contexto_xml", ""),
                alias,
                alias,
                f"{aparicion.get('referencia_original', '')}: "
                f"{aparicion.get('contenido_original', '')}",
                nodo_origen=("VARIABLE", alias),
                nodo_destino=("VARIABLE", alias),
            )
        no_resueltos.extend(localizacion.get("errores", []))

    for referencia in resultado.get("referencias_no_resueltas", []):
        no_resueltos.append(
            f"Subproceso no resuelto en {referencia.get('job_origen', '')} / "
            f"{referencia.get('entrada', '')}; el linaje no puede continuar por esa rama."
        )
    if any(
        relacion.get("estado") == "ciclo_detectado"
        for relacion in resultado.get("relaciones", [])
    ):
        no_resueltos.append("Existe un ciclo de procesos; el recorrido se presenta una sola vez.")

    no_resueltos = list(dict.fromkeys(no_resueltos))
    if origenes_kettle and no_resueltos:
        estado_origen = "parcialmente_resuelto"
    elif origenes_kettle:
        estado_origen = "resuelto_kettle"
    elif resolucion["origenes_internos"] and not no_resueltos:
        estado_origen = "resuelto_interno"
    else:
        estado_origen = "no_resuelto"
    procesos_involucrados = list(
        dict.fromkeys(
            evento["proceso"]
            for evento in eventos
            if evento.get("proceso")
            and evento["proceso"] != "-"
            and evento.get("tipo_evento") != "ORIGEN_KETTLE"
        )
    )
    return {
        "variable_seleccionada": variable,
        "origen_kettle": origenes_kettle[0] if origenes_kettle else None,
        "origenes_kettle": origenes_kettle,
        "estado_origen": estado_origen,
        "aliases": aliases,
        "campos_flujo": campos_flujo,
        "procesos_involucrados": procesos_involucrados,
        "variables_creadas_renombradas": {
            nombre: modelo["creaciones"][nombre]
            for nombre in aliases
            if nombre in modelo["creaciones"]
        },
        "eventos": eventos,
        "no_resueltos": no_resueltos,
    }


def construir_linaje_variable(
    variable: str,
    resultado: dict[str, Any],
    inventario: dict[str, Any],
    propiedades_kettle: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reconstruye ramas independientes y resuelve el origen al final de cada una."""
    propiedades_kettle = propiedades_kettle or {}
    variable = normalizar_clave_kettle(variable)
    indice_kettle = crear_indice_kettle_normalizado(propiedades_kettle)
    modelo = construir_modelo_linaje(resultado, inventario, propiedades_kettle)
    resolucion = _resolver_dependencias_modelo(modelo, variable, indice_kettle)
    rutas = _enumerar_rutas_origen_modelo(modelo, variable, indice_kettle)
    eventos: list[dict[str, Any]] = []
    no_resueltos: list[str] = []
    avisos: list[str] = []
    origenes_kettle: list[dict[str, Any]] = []
    referencias_cubiertas: set[tuple[str, str, str]] = set()

    def agregar(
        tipo: str,
        proceso: str,
        componente: str,
        origen: str,
        destino: str,
        detalle: str,
        estado: str = "resuelto",
        **extras: Any,
    ) -> None:
        eventos.append(
            {
                "orden": len(eventos) + 1,
                "tipo_evento": tipo,
                "proceso": proceso,
                "componente": componente,
                "variable_origen": origen,
                "variable_destino": destino,
                "detalle": detalle,
                "estado": estado,
                **extras,
            }
        )

    inicio = resolucion["inicio"]
    nodos_adelante: set[tuple[str, ...]] = {inicio}
    aristas_adelante: dict[str, dict[str, Any]] = {}
    distancias_adelante: dict[tuple[str, ...], int] = {inicio: 0}
    cola = deque([inicio])
    while cola:
        nodo = cola.popleft()
        for arista in modelo["salientes"].get(nodo, []):
            aristas_adelante[arista["id"]] = arista
            destino = arista["destino_id"]
            nodos_adelante.add(destino)
            distancia = distancias_adelante[nodo] + 1
            if destino not in distancias_adelante or distancia < distancias_adelante[destino]:
                distancias_adelante[destino] = distancia
                cola.append(destino)

    ids_aristas_rutas = {
        arista["id"] for ruta in rutas for arista in ruta.get("aristas", [])
    }
    aristas_posteriores = sorted(
        (
            arista
            for arista in aristas_adelante.values()
            if arista["id"] not in ids_aristas_rutas
        ),
        key=lambda item: (
            distancias_adelante.get(item["origen_id"], 10**6),
            distancias_adelante.get(item["destino_id"], 10**6),
            item.get("indice", 0),
        ),
    )

    def agregar_arista_ruta(arista: dict[str, Any], ruta_origen: str) -> None:
        agregar(
            arista.get("tipo_evento", "DERIVACION"),
            arista.get("proceso", ""),
            arista.get("componente", ""),
            arista.get("variable_origen", ""),
            arista.get("variable_destino", ""),
            arista.get("detalle", ""),
            arista.get("estado", "resuelto"),
            ruta_origen=ruta_origen,
            nodo_origen=arista.get("origen_id"),
            nodo_destino=arista.get("destino_id"),
            alcance=arista.get("alcance", ""),
            confianza=arista.get("confianza", "alta"),
            origen_tipo=arista.get("origen_tipo", ""),
            destino_tipo=arista.get("destino_tipo", ""),
            proceso_id=arista.get("proceso_id", ""),
            proceso_destino_id=arista.get("destino_proceso_id", ""),
            proceso_destino=arista.get("proceso_destino", ""),
            contexto=arista.get("contexto_xml", ""),
            valor_configurado=arista.get("valor_configurado", ""),
        )
        if arista.get("origen_tipo") == "VARIABLE":
            referencias_cubiertas.add(
                (
                    arista.get("proceso_id", ""),
                    arista.get("componente", ""),
                    normalizar_clave_kettle(arista.get("variable_origen", "")),
                )
            )

    for ruta in rutas:
        ruta_origen = ruta["ruta_origen"]
        tipo_origen = ruta["tipo_origen"]
        datos_origen = ruta["datos_origen"]
        nodo_origen = ruta["nodo_origen"]
        if tipo_origen == "kettle":
            propiedad = _datos_kettle_seguros(datos_origen)
            propiedad["ruta_origen"] = ruta_origen
            origenes_kettle.append(propiedad)
            nombre_propiedad = propiedad.get("nombre", nodo_origen[-1])
            agregar(
                "ORIGEN_KETTLE",
                propiedad.get("archivo", "kettle.properties"),
                f"línea {propiedad.get('linea', '')}",
                nombre_propiedad,
                nombre_propiedad,
                f"Valor: {propiedad.get('valor_enmascarado', '[valor oculto]')}",
                ruta_origen=ruta_origen,
                nodo_origen=nodo_origen,
                nodo_destino=nodo_origen,
                archivo_origen=propiedad.get("archivo", "kettle.properties"),
                linea_origen=propiedad.get("linea"),
                valor_enmascarado=propiedad.get("valor_enmascarado"),
                confianza="alta",
            )
        elif tipo_origen == "parametro":
            nombre_parametro = normalizar_clave_kettle(
                datos_origen.get("nombre", nodo_origen[-1])
            )
            agregar(
                "ORIGEN_PARAMETRO",
                datos_origen.get("proceso", "-"),
                datos_origen.get("componente", "parameters"),
                nombre_parametro,
                nombre_parametro,
                "El origen es un parámetro declarado por el proceso.",
                ruta_origen=ruta_origen,
                nodo_origen=nodo_origen,
                nodo_destino=nodo_origen,
                confianza="alta",
            )
        elif tipo_origen == "interno":
            agregar(
                "ORIGEN_INTERNO",
                datos_origen.get("proceso", "-"),
                datos_origen.get("componente", "-"),
                datos_origen.get("nombre", ""),
                variable,
                "El valor se define dentro del proceso y no procede de kettle.properties.",
                ruta_origen=ruta_origen,
                nodo_origen=nodo_origen,
                nodo_destino=inicio,
                confianza="alta",
            )
        elif tipo_origen == "no_resuelto":
            nombre_raiz = normalizar_clave_kettle(datos_origen.get("nombre", variable))
            if datos_origen.get("tipo") == "CAMPO_FLUJO":
                mensaje = (
                    f"No se identificó un productor alcanzable para el campo {nombre_raiz}."
                )
            else:
                mensaje = (
                    f"No existe origen Kettle, parámetro, asignación local ni origen dinámico "
                    f"para {nombre_raiz}."
                )
            no_resueltos.append(mensaje)
            agregar(
                "ORIGEN_NO_RESUELTO",
                datos_origen.get("proceso", "-"),
                datos_origen.get("componente", "-"),
                nombre_raiz,
                variable,
                mensaje,
                "no_resuelto",
                ruta_origen=ruta_origen,
                nodo_origen=nodo_origen,
                nodo_destino=inicio,
                clave_buscada_repr=repr(nombre_raiz),
                confianza="baja",
            )

        ids_agregados: set[str] = set()
        for arista in [*ruta.get("aristas", []), *aristas_posteriores]:
            if arista["id"] in ids_agregados:
                continue
            ids_agregados.add(arista["id"])
            agregar_arista_ruta(arista, ruta_origen)

    nodos_relevantes = set(resolucion["nodos_aguas_arriba"]) | nodos_adelante
    aliases = sorted(
        {
            normalizar_clave_kettle(nodo[1])
            for nodo in nodos_relevantes
            if nodo and nodo[0] == "VARIABLE" and len(nodo) > 1 and nodo[1]
        },
        key=lambda nombre: (nombre.lower(), nombre),
    )
    campos_flujo = sorted(
        {
            modelo["nodos"].get(nodo, {}).get("nombre", "")
            for nodo in nodos_relevantes
            if nodo and nodo[0] == "CAMPO_FLUJO"
        }
        - {""},
        key=lambda nombre: (nombre.lower(), nombre),
    )

    ids_todas_rutas = [ruta["ruta_origen"] for ruta in rutas]
    for alias in aliases:
        localizacion = localizar_variable(
            alias, resultado, inventario, propiedades_kettle
        )
        for aparicion in localizacion.get("apariciones", []):
            if aparicion.get("tipo_aparicion") not in {
                "REFERENCIA_VARIABLE",
                "REFERENCIA_VARIABLE_KETTLE",
            }:
                continue
            clave_cubierta = (
                aparicion.get("proceso_id", ""),
                aparicion.get("componente") or "",
                alias,
            )
            if clave_cubierta in referencias_cubiertas:
                continue
            nodo_alias = ("VARIABLE", alias)
            rutas_alias = [
                ruta["ruta_origen"]
                for ruta in rutas
                if nodo_alias in ruta.get("nodos", []) or nodo_alias in nodos_adelante
            ] or ["global"]
            for ruta_origen in dict.fromkeys(rutas_alias):
                agregar(
                    "USO",
                    aparicion["proceso"],
                    aparicion.get("componente") or aparicion.get("contexto_xml", ""),
                    alias,
                    alias,
                    f"{aparicion.get('referencia_original', '')}: "
                    f"{aparicion.get('contenido_original', '')}",
                    ruta_origen=ruta_origen,
                    nodo_origen=nodo_alias,
                    nodo_destino=nodo_alias,
                    confianza="alta",
                )
        avisos.extend(localizacion.get("errores", []))

    for referencia in resultado.get("referencias_no_resueltas", []):
        avisos.append(
            f"Subproceso no resuelto en {referencia.get('job_origen', '')} / "
            f"{referencia.get('entrada', '')}; no se recorrió esa rama de procesos."
        )
    if any(
        relacion.get("estado") == "ciclo_detectado"
        for relacion in resultado.get("relaciones", [])
    ):
        avisos.append("Existe un ciclo de procesos; cada ruta de linaje se presenta una sola vez.")

    no_resueltos = list(dict.fromkeys(no_resueltos))
    avisos = list(dict.fromkeys(avisos))
    total_rutas = len(rutas)
    rutas_kettle = sum(ruta["tipo_origen"] == "kettle" for ruta in rutas)
    rutas_dinamicas = sum(ruta["tipo_origen"] == "dinamico" for ruta in rutas)
    rutas_internas = sum(ruta["tipo_origen"] == "interno" for ruta in rutas)
    rutas_parametros = sum(ruta["tipo_origen"] == "parametro" for ruta in rutas)
    rutas_no_resueltas = sum(ruta["tipo_origen"] == "no_resuelto" for ruta in rutas)
    tiene_derivacion_kettle = any(
        ruta["tipo_origen"] == "kettle" and ruta.get("aristas") for ruta in rutas
    )

    if rutas_no_resueltas == total_rutas:
        estado_origen = "no_resuelto"
    elif rutas_no_resueltas:
        estado_origen = "parcialmente_resuelto"
    elif rutas_kettle and total_rutas > 1:
        estado_origen = "resuelto_kettle_multiple"
    elif rutas_kettle:
        estado_origen = "resuelto_kettle"
    elif rutas_dinamicas == total_rutas:
        estado_origen = "resuelto_dinamico"
    elif rutas_parametros == total_rutas:
        estado_origen = "resuelto_parametro"
    elif rutas_internas == total_rutas:
        estado_origen = "resuelto_interno"
    else:
        estado_origen = "resuelto_multiple"

    if total_rutas > 1:
        tipo_origen = "condicional_o_multiple"
    elif rutas_kettle:
        tipo_origen = "kettle_derivado" if tiene_derivacion_kettle else "kettle_directo"
    elif rutas_dinamicas:
        tipo_origen = "dinamico"
    elif rutas_parametros:
        tipo_origen = "parametro"
    elif rutas_internas:
        tipo_origen = "interno"
    else:
        tipo_origen = "no_resuelto"

    if rutas_kettle and rutas_kettle == total_rutas:
        origen_resumen = "Kettle derivado" if tiene_derivacion_kettle else "Kettle directo"
    elif rutas_dinamicas and rutas_dinamicas == total_rutas:
        origen_resumen = "Resultado dinámico"
    elif rutas_parametros and rutas_parametros == total_rutas:
        origen_resumen = "Parámetro del proceso"
    elif rutas_internas and rutas_internas == total_rutas:
        origen_resumen = "Origen interno"
    elif rutas_no_resueltas == total_rutas:
        origen_resumen = "No resuelto"
    else:
        origen_resumen = "Origen mixto"

    resumen_rutas = []
    for ruta in rutas:
        ruta_id = ruta["ruta_origen"]
        resumen_rutas.append(
            {
                "ruta_origen": ruta_id,
                "tipo_origen": ruta["tipo_origen"],
                "origen": ruta["datos_origen"].get("nombre", ""),
                "estado": "no_resuelto" if ruta["tipo_origen"] == "no_resuelto" else "resuelto",
                "eventos": [
                    evento["orden"]
                    for evento in eventos
                    if evento.get("ruta_origen") == ruta_id
                ],
            }
        )

    procesos_involucrados = list(
        dict.fromkeys(
            evento["proceso"]
            for evento in eventos
            if evento.get("proceso")
            and evento["proceso"] != "-"
            and evento.get("tipo_evento") != "ORIGEN_KETTLE"
        )
    )
    resumen_origen = {
        "origen": origen_resumen,
        "tipo": tipo_origen,
        "cantidad_rutas": total_rutas,
        "rutas_resueltas_kettle": rutas_kettle,
        "rutas_dinamicas": rutas_dinamicas,
        "rutas_internas": rutas_internas,
        "rutas_parametros": rutas_parametros,
        "rutas_no_resueltas": rutas_no_resueltas,
    }
    return {
        "variable_seleccionada": variable,
        "origen": origen_resumen,
        "tipo_origen": tipo_origen,
        "origen_kettle": origenes_kettle[0] if origenes_kettle else None,
        "origenes_kettle": origenes_kettle,
        "estado_origen": estado_origen,
        "resumen_origen": resumen_origen,
        "rutas_origen": resumen_rutas,
        "aliases": aliases,
        "campos_flujo": campos_flujo,
        "procesos_involucrados": procesos_involucrados,
        "variables_creadas_renombradas": {
            nombre: modelo["creaciones"][nombre]
            for nombre in aliases
            if nombre in modelo["creaciones"]
        },
        "eventos": eventos,
        "no_resueltos": no_resueltos,
        "avisos": avisos,
        "diagnostico_busquedas_kettle": {
            "claves_no_resueltas_repr": [
                evento.get("clave_buscada_repr")
                for evento in eventos
                if evento.get("tipo_evento") == "ORIGEN_NO_RESUELTO"
            ],
            "primeras_claves_disponibles": list(indice_kettle)[:5],
        },
    }


def crear_linaje_parametro_vacio() -> dict[str, Any]:
    return {
        "parametro_seleccionado": None,
        "proceso_id_seleccionado": None,
        "nodos_parametro": [],
        "procesos_involucrados": [],
        "estado_origen": None,
        "eventos": [],
        "recorrido": [],
        "no_resueltos": [],
        "avisos": [],
    }


def construir_modelo_linaje_parametros(
    resultado: dict[str, Any],
    inventario: dict[str, Any],
    propiedades_kettle: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Expone la vista tipada de parámetros sobre el motor de linaje existente."""
    modelo = construir_modelo_linaje(resultado, inventario, propiedades_kettle)
    nodos_parametro = {
        nodo_id: datos
        for nodo_id, datos in modelo.get("nodos", {}).items()
        if datos.get("tipo") == "PARAMETRO"
    }
    aristas_parametro = [
        arista
        for arista in modelo.get("aristas", [])
        if arista.get("origen_id") in nodos_parametro
        or arista.get("destino_id") in nodos_parametro
    ]
    return {
        **modelo,
        "nodos_parametro": nodos_parametro,
        "aristas_parametro": aristas_parametro,
    }


def construir_linaje_parametro(
    parametro: str,
    resultado: dict[str, Any],
    inventario: dict[str, Any],
    proceso_id: str | None = None,
    propiedades_kettle: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    parametro = normalizar_clave_kettle(parametro)
    informacion = resultado.get("parametros") or crear_informacion_parametros_vacia()
    indice_kettle = crear_indice_kettle_normalizado(propiedades_kettle or {})
    modelo = construir_modelo_linaje_parametros(
        resultado, inventario, propiedades_kettle
    )
    candidatos = {
        nodo_id
        for nodo_id, datos in modelo.get("nodos_parametro", {}).items()
        if normalizar_clave_kettle(datos.get("nombre", "")) == parametro
        and (proceso_id is None or datos.get("proceso_id") == proceso_id)
    }
    seleccionados = set(candidatos)
    cambio = True
    while cambio:
        cambio = False
        for arista in modelo.get("aristas_parametro", []):
            origen_id = arista.get("origen_id")
            destino_id = arista.get("destino_id")
            if destino_id in seleccionados and origen_id and origen_id[0] == "PARAMETRO":
                if origen_id not in seleccionados:
                    seleccionados.add(origen_id)
                    cambio = True
            if origen_id in seleccionados and destino_id and destino_id[0] == "PARAMETRO":
                if destino_id not in seleccionados:
                    seleccionados.add(destino_id)
                    cambio = True

    eventos: list[dict[str, Any]] = []
    no_resueltos: list[str] = []
    avisos: list[str] = []

    def agregar(
        tipo_evento: str,
        proceso: str,
        componente: str,
        origen: str,
        destino: str,
        detalle: str,
        estado: str = "resuelto",
        **extras: Any,
    ) -> None:
        eventos.append(
            {
                "orden": len(eventos) + 1,
                "tipo_evento": tipo_evento,
                "proceso": proceso,
                "componente": componente,
                "origen": origen,
                "destino": destino,
                "estado": estado,
                "detalle": detalle,
                **extras,
            }
        )

    for nodo_id in sorted(seleccionados, key=lambda item: (item[1], item[2])):
        datos = modelo["nodos"].get(nodo_id, {})
        if not datos.get("declarado"):
            continue
        agregar(
            "DECLARACION_PARAMETRO",
            datos.get("proceso", ""),
            "parameters",
            "",
            datos.get("nombre", ""),
            datos.get("descripcion", "") or "Parámetro declarado por el proceso.",
            proceso_id=datos.get("proceso_id", ""),
            ruta=datos.get("ruta", ""),
            origen_tipo=None,
            destino_tipo="PARAMETRO",
            nodo_origen=None,
            nodo_destino=nodo_id,
        )
        if datos.get("tiene_valor_default", bool(datos.get("valor_default", ""))):
            valor_default = datos.get("valor_default", "")
            agregar(
                "VALOR_PREDETERMINADO",
                datos.get("proceso", ""),
                "parameters",
                valor_default,
                datos.get("nombre", ""),
                f"Valor por defecto: {valor_default or '(vacío)'}",
                proceso_id=datos.get("proceso_id", ""),
                ruta=datos.get("ruta", ""),
                valor_configurado=valor_default,
                origen_tipo=(
                    "DINAMICO"
                    if extraer_nombres_referenciados(valor_default)
                    else "LITERAL"
                ),
                destino_tipo="PARAMETRO",
                nodo_origen=None,
                nodo_destino=nodo_id,
            )

    ejecuciones_script_vistas: set[tuple[str, int | None, str]] = set()
    for asignacion in informacion.get("asignaciones_desde_scripts", []):
        destino_id = (
            "PARAMETRO",
            asignacion.get("proceso_destino_id", ""),
            normalizar_clave_kettle(asignacion.get("parametro_destino", "")),
        )
        if destino_id not in seleccionados:
            continue
        clave_ejecucion = (
            asignacion.get("script", ""),
            asignacion.get("linea"),
            asignacion.get("proceso_destino_id", ""),
        )
        if clave_ejecucion not in ejecuciones_script_vistas:
            ejecuciones_script_vistas.add(clave_ejecucion)
            agregar(
                "SCRIPT_EJECUTA_JOB",
                asignacion.get("script", ""),
                "kitchen.sh",
                asignacion.get("script", ""),
                asignacion.get("proceso_destino", ""),
                "El script ejecuta el Job vinculado mediante kitchen.sh.",
                proceso_id=asignacion.get("proceso_destino_id", ""),
                ruta=asignacion.get("ruta_script", ""),
                ruta_destino=asignacion.get("ruta_destino", ""),
                linea=asignacion.get("linea"),
                origen_tipo="SCRIPT_SHELL",
                destino_tipo="JOB",
                nodo_origen=None,
                nodo_destino=None,
            )
        for variable_shell in asignacion.get("variables_shell", []):
            agregar(
                "VARIABLE_SHELL",
                asignacion.get("script", ""),
                "kitchen.sh -param",
                variable_shell,
                asignacion.get("origen", ""),
                (
                    f"{variable_shell} forma parte del origen Shell del "
                    f"parámetro {asignacion.get('parametro_destino', '')}."
                ),
                proceso_id=asignacion.get("proceso_destino_id", ""),
                linea=asignacion.get("linea"),
                origen_tipo="VARIABLE_SHELL",
                destino_tipo=asignacion.get("origen_tipo", ""),
                nodo_origen=None,
                nodo_destino=None,
            )
        agregar(
            asignacion.get("origen_tipo", "LITERAL_SCRIPT"),
            asignacion.get("script", ""),
            "kitchen.sh -param",
            asignacion.get("origen", ""),
            asignacion.get("parametro_destino", ""),
            "Origen estático detectado en el script sin evaluar la expresión.",
            proceso_id=asignacion.get("proceso_destino_id", ""),
            linea=asignacion.get("linea"),
            valor_configurado=asignacion.get("valor_configurado", ""),
            origen_tipo=asignacion.get("origen_tipo", ""),
            destino_tipo="PARAMETRO",
            nodo_origen=None,
            nodo_destino=destino_id,
        )

    aristas_vistas: set[str] = set()
    origenes_kettle_vistos: set[tuple[str, tuple[str, ...]]] = set()
    for arista in modelo.get("aristas_parametro", []):
        origen_id = arista.get("origen_id")
        destino_id = arista.get("destino_id")
        if destino_id not in seleccionados and origen_id not in seleccionados:
            continue
        if (
            arista.get("tipo_evento") == "VALOR_PREDETERMINADO"
            and arista.get("componente") == "parameters"
        ):
            continue
        if arista.get("id") in aristas_vistas:
            continue
        aristas_vistas.add(arista.get("id", ""))
        tipo_evento = arista.get("tipo_evento", "ORIGEN_PARAMETRO_NO_RESUELTO")
        estado = arista.get("estado", "resuelto")
        if estado != "resuelto":
            mensaje = arista.get("detalle", "Origen de parámetro no resuelto.")
            if mensaje not in no_resueltos:
                no_resueltos.append(mensaje)
        if arista.get("origen_tipo") == "VARIABLE" and destino_id in seleccionados:
            nombre_variable = normalizar_clave_kettle(
                arista.get("variable_origen", "")
            )
            propiedad = buscar_variable_en_kettle(nombre_variable, indice_kettle)
            clave_kettle = (nombre_variable, destino_id)
            if propiedad is not None and clave_kettle not in origenes_kettle_vistos:
                origenes_kettle_vistos.add(clave_kettle)
                segura = _datos_kettle_seguros(propiedad)
                agregar(
                    "ORIGEN_KETTLE",
                    segura.get("archivo", "kettle.properties"),
                    f"línea {segura.get('linea', '')}",
                    nombre_variable,
                    nombre_variable,
                    f"Valor: {segura.get('valor_enmascarado', '[valor oculto]')}",
                    proceso_id=arista.get("proceso_id", ""),
                    origen_tipo="VARIABLE_KETTLE",
                    destino_tipo="VARIABLE",
                    nodo_origen=origen_id,
                    nodo_destino=origen_id,
                    confianza="alta",
                    valor_enmascarado=segura.get("valor_enmascarado", ""),
                )
        agregar(
            tipo_evento,
            arista.get("proceso", ""),
            arista.get("componente", ""),
            arista.get("variable_origen", ""),
            arista.get("variable_destino", ""),
            arista.get("detalle", ""),
            estado,
            proceso_id=arista.get("proceso_id", ""),
            proceso_destino=arista.get("proceso_destino", ""),
            proceso_destino_id=arista.get("destino_proceso_id", ""),
            ruta=arista.get("ruta", ""),
            ruta_destino=arista.get("ruta_destino", ""),
            valor_configurado=arista.get("valor_configurado", ""),
            origen_tipo=arista.get("origen_tipo", ""),
            destino_tipo=arista.get("destino_tipo", ""),
            confianza=arista.get("confianza", ""),
            contexto=arista.get("contexto_xml", ""),
            script=arista.get("script", ""),
            linea=arista.get("linea"),
            variables_shell=arista.get("variables_shell", []),
            nodo_origen=origen_id,
            nodo_destino=destino_id,
        )
        if destino_id in seleccionados and arista.get("componente") != "parameters":
            agregar(
                "RECIBE_DESDE_PROCESO_PADRE",
                arista.get("proceso_destino", ""),
                arista.get("componente", ""),
                arista.get("variable_origen", ""),
                arista.get("variable_destino", ""),
                arista.get("detalle", ""),
                estado,
                proceso_id=arista.get("destino_proceso_id", ""),
                proceso_origen=arista.get("proceso", ""),
                origen_tipo=arista.get("origen_tipo", ""),
                destino_tipo="PARAMETRO",
                nodo_origen=origen_id,
                nodo_destino=destino_id,
            )
        if (
            origen_id in seleccionados
            and destino_id
            and destino_id[0] == "PARAMETRO"
            and origen_id[1] != destino_id[1]
            and arista.get("componente") != "parameters"
        ):
            agregar(
                "ENVIA_A_SUBPROCESO",
                arista.get("proceso", ""),
                arista.get("componente", ""),
                arista.get("variable_origen", ""),
                arista.get("variable_destino", ""),
                arista.get("detalle", ""),
                estado,
                proceso_id=arista.get("proceso_id", ""),
                proceso_destino=arista.get("proceso_destino", ""),
                origen_tipo="PARAMETRO",
                destino_tipo="PARAMETRO",
                nodo_origen=origen_id,
                nodo_destino=destino_id,
            )

    for nodo_id in seleccionados:
        datos = modelo.get("nodos", {}).get(nodo_id, {})
        if not datos.get("declarado") or datos.get(
            "tiene_valor_default", bool(datos.get("valor_default", ""))
        ):
            continue
        if modelo.get("entrantes", {}).get(nodo_id):
            continue
        mensaje = (
            f"El parámetro {datos.get('nombre', '')} está declarado en "
            f"{datos.get('proceso', '')}, pero no tiene valor por defecto ni "
            "asignación explícita desde un proceso padre."
        )
        no_resueltos.append(mensaje)
        agregar(
            "ORIGEN_PARAMETRO_NO_RESUELTO",
            datos.get("proceso", ""),
            "parameters",
            "",
            datos.get("nombre", ""),
            mensaje,
            "no_resuelto",
            proceso_id=datos.get("proceso_id", ""),
            ruta=datos.get("ruta", ""),
            origen_tipo="DESCONOCIDO",
            destino_tipo="PARAMETRO",
            nodo_origen=None,
            nodo_destino=nodo_id,
            confianza="baja",
        )

    nombres_por_id = {
        (nodo_id[1], nodo_id[2]) for nodo_id in seleccionados
    }
    for referencia in informacion.get("referencias", []):
        clave = (
            referencia.get("proceso_id", ""),
            normalizar_clave_kettle(referencia.get("nombre", "")),
        )
        if clave not in nombres_por_id:
            continue
        nodo_id = ("PARAMETRO", clave[0], clave[1])
        agregar(
            "USO_PARAMETRO",
            referencia.get("proceso", ""),
            referencia.get("componente") or referencia.get("contexto_xml", ""),
            referencia.get("nombre", ""),
            referencia.get("nombre", ""),
            (
                f"{referencia.get('referencia', '')}: "
                f"{referencia.get('contenido_original', '')}"
            ),
            proceso_id=referencia.get("proceso_id", ""),
            ruta=referencia.get("ruta", ""),
            origen_tipo="PARAMETRO",
            destino_tipo="USO",
            contexto=referencia.get("contexto_xml", ""),
            nodo_origen=nodo_id,
            nodo_destino=nodo_id,
        )

    if not candidatos:
        no_resueltos.append(
            f"No se encontró el parámetro {parametro} dentro del subgrafo seleccionado."
        )
    if no_resueltos and eventos:
        estado_origen = "parcialmente_resuelto"
    elif no_resueltos:
        estado_origen = "no_resuelto"
    else:
        estado_origen = "resuelto"
    procesos_involucrados = list(
        dict.fromkeys(evento.get("proceso", "") for evento in eventos if evento.get("proceso"))
    )
    recorrido = [
        {
            "orden": evento["orden"],
            "texto": (
                f"{evento['tipo_evento']}: {evento.get('origen', '')} "
                f"→ {evento.get('destino', '')}"
            ),
        }
        for evento in eventos
    ]
    return {
        "parametro_seleccionado": parametro,
        "proceso_id_seleccionado": proceso_id,
        "nodos_parametro": [list(nodo_id) for nodo_id in sorted(seleccionados)],
        "procesos_involucrados": procesos_involucrados,
        "estado_origen": estado_origen,
        "eventos": eventos,
        "recorrido": recorrido,
        "no_resueltos": no_resueltos,
        "avisos": avisos,
    }


def obtener_objeto_inventario(
    tipo: str, proceso_id: str, inventario: dict[str, Any]
) -> dict[str, Any] | None:
    if tipo == "JOB":
        return inventario["jobs"].get(proceso_id)
    if tipo == "TRANSFORMATION":
        return inventario["transformaciones"].get(proceso_id)
    return None


def resolver_llamada_entrada(
    entrada: dict[str, Any], inventario: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    tipo_esperado = entrada.get("tipo_esperado", "UNKNOWN")
    if tipo_esperado == "JOB":
        busqueda = buscar_job_referenciado(entrada_a_referencia(entrada), inventario)
    elif tipo_esperado == "TRANSFORMATION":
        busqueda = buscar_transformacion_referenciada(entrada_a_referencia(entrada), inventario)
    else:
        return None, ""
    if busqueda.get("estado") == "resuelta":
        return busqueda["coincidencias"][0], "resuelta"
    return None, busqueda.get("estado", "referencia_no_resuelta")


def construir_grafo_proceso(objeto: dict[str, Any], inventario: dict[str, Any]) -> dict[str, Any]:
    nodos: dict[str, dict[str, Any]] = {}
    orden_nodos: list[str] = []
    avisos: list[str] = []

    if objeto["tipo"] == "JOB":
        for entrada in objeto.get("entradas", []):
            nombre = entrada["nombre"]
            if nombre in nodos:
                avisos.append(f"[NOMBRE DE NODO DUPLICADO] {nombre}")
                continue
            if entrada.get("habilitada", True):
                destino, estado_referencia = resolver_llamada_entrada(entrada, inventario)
            else:
                destino, estado_referencia = None, "deshabilitada"
            nodos[nombre] = {
                "nombre": nombre,
                "tipo": entrada.get("tipo_entrada") or "ENTRY",
                "indice": entrada.get("indice", 0),
                "habilitado": entrada.get("habilitada", True),
                "es_llamada_proceso": destino is not None,
                "proceso_referenciado": (
                    {
                        "id": destino["id"],
                        "nombre": destino["nombre"],
                        "tipo": destino["tipo"],
                        "ruta": destino["ruta"],
                    }
                    if destino
                    else None
                ),
                "estado_referencia": estado_referencia,
            }
            orden_nodos.append(nombre)
        hops = extraer_hops_job(objeto["xml_element"])
    else:
        for paso in extraer_pasos_transformacion(objeto["xml_element"]):
            nombre = paso["nombre"]
            if nombre in nodos:
                avisos.append(f"[NOMBRE DE NODO DUPLICADO] {nombre}")
                continue
            nodos[nombre] = {
                "nombre": nombre,
                "tipo": paso.get("tipo_entrada") or "STEP",
                "indice": paso.get("indice", 0),
                "habilitado": True,
                "es_llamada_proceso": False,
                "proceso_referenciado": None,
                "estado_referencia": "",
            }
            orden_nodos.append(nombre)
        hops = extraer_hops_transformacion(objeto["xml_element"])

    sucesores = {nombre: set() for nombre in nodos}
    predecesores = {nombre: set() for nombre in nodos}
    relaciones: list[dict[str, str]] = []
    relaciones_vistas: set[tuple[str, str]] = set()
    for hop in hops:
        if es_falso(hop.get("habilitado", "Y")):
            continue
        origen = hop.get("origen", "")
        destino = hop.get("destino", "")
        if origen not in nodos or destino not in nodos:
            avisos.append(f"[HOP CON NODO NO ENCONTRADO] {origen} -> {destino}")
            continue
        if (origen, destino) in relaciones_vistas:
            continue
        relaciones_vistas.add((origen, destino))
        sucesores[origen].add(destino)
        predecesores[destino].add(origen)
        relaciones.append({"origen": origen, "destino": destino})

    if not relaciones:
        avisos.append("[PROCESO SIN HOPS]")

    inicios_sin_predecesores = [nombre for nombre in orden_nodos if not predecesores[nombre]]
    starts = [nombre for nombre in orden_nodos if nombre.strip().upper() == "START"]
    nodos_iniciales = starts or inicios_sin_predecesores
    if len(nodos_iniciales) > 1:
        avisos.append("[MÚLTIPLES NODOS INICIALES]")

    niveles, nodos_sin_nivel = calcular_niveles_estructurales(
        orden_nodos, sucesores, predecesores
    )
    ciclos = detectar_ciclos_grafo(orden_nodos, sucesores)
    return {
        "proceso": objeto,
        "nodos": nodos,
        "orden_nodos": orden_nodos,
        "sucesores": sucesores,
        "predecesores": predecesores,
        "relaciones": relaciones,
        "nodos_iniciales": nodos_iniciales,
        "niveles": niveles,
        "nodos_sin_nivel": nodos_sin_nivel,
        "ciclos": ciclos,
        "avisos": avisos,
    }


def calcular_niveles_estructurales(
    orden_nodos: list[str],
    sucesores: dict[str, set[str]],
    predecesores: dict[str, set[str]],
) -> tuple[dict[str, int], list[str]]:
    grados = {nombre: len(predecesores[nombre]) for nombre in orden_nodos}
    niveles = {nombre: 0 for nombre in orden_nodos if grados[nombre] == 0}
    pendientes = deque(nombre for nombre in orden_nodos if grados[nombre] == 0)

    while pendientes:
        origen = pendientes.popleft()
        for destino in sucesores[origen]:
            niveles[destino] = max(niveles.get(destino, 0), niveles[origen] + 1)
            grados[destino] -= 1
            if grados[destino] == 0:
                pendientes.append(destino)

    nodos_sin_nivel = [nombre for nombre in orden_nodos if grados[nombre] > 0]
    return niveles, nodos_sin_nivel


def detectar_ciclos_grafo(
    orden_nodos: list[str], sucesores: dict[str, set[str]]
) -> list[list[str]]:
    estado: dict[str, int] = {nombre: 0 for nombre in orden_nodos}
    pila: list[str] = []
    posicion_pila: dict[str, int] = {}
    ciclos: list[list[str]] = []
    firmas: set[tuple[str, ...]] = set()

    def visitar(nodo: str) -> None:
        estado[nodo] = 1
        posicion_pila[nodo] = len(pila)
        pila.append(nodo)
        for sucesor in sucesores[nodo]:
            if estado[sucesor] == 0:
                visitar(sucesor)
            elif estado[sucesor] == 1:
                ciclo = pila[posicion_pila[sucesor] :] + [sucesor]
                base = ciclo[:-1]
                if base:
                    rotaciones = [tuple(base[indice:] + base[:indice]) for indice in range(len(base))]
                    firma = min(rotaciones)
                    if firma not in firmas:
                        firmas.add(firma)
                        ciclos.append(ciclo)
        pila.pop()
        posicion_pila.pop(nodo, None)
        estado[nodo] = 2

    for nombre in orden_nodos:
        if estado[nombre] == 0:
            visitar(nombre)
    return ciclos


def obtener_predecesores_hasta_nodo(
    nodo_objetivo: str, predecesores: dict[str, set[str]]
) -> set[str]:
    if nodo_objetivo not in predecesores:
        return set()
    visitados: set[str] = set()
    pendientes = [nodo_objetivo]
    while pendientes:
        nodo = pendientes.pop()
        if nodo in visitados:
            continue
        visitados.add(nodo)
        pendientes.extend(predecesores[nodo] - visitados)
    return visitados


def construir_ocurrencias_proceso(
    arbol: dict[str, Any],
    inventario: dict[str, Any],
    ids_permitidos: set[str] | None = None,
) -> list[dict[str, Any]]:
    ocurrencias: list[dict[str, Any]] = []

    def recorrer(
        objeto: dict[str, Any],
        parent_id: str | None,
        ruta: list[str],
        posicion: tuple[int, ...],
        entrada_llamada: dict[str, Any] | None,
        procesos_en_ruta: set[str],
    ) -> None:
        ocurrencia_id = "/".join(str(indice) for indice in posicion)
        ruta_actual = [*ruta, objeto["nombre"]]
        es_recursiva = objeto["id"] in procesos_en_ruta
        ocurrencia = {
            "ocurrencia_id": ocurrencia_id,
            "proceso_id": objeto["id"],
            "proceso": objeto["nombre"],
            "tipo": objeto["tipo"],
            "ruta": objeto["ruta"],
            "ruta_ocurrencia": ruta_actual,
            "parent_ocurrencia_id": parent_id,
            "entrada_padre": entrada_llamada.get("nombre", "") if entrada_llamada else "",
            "entrada_padre_indice": entrada_llamada.get("indice", 0) if entrada_llamada else 0,
            "estado": "dependencia_recursiva" if es_recursiva else "resuelta",
        }
        ocurrencias.append(ocurrencia)

        if es_recursiva or objeto["tipo"] != "JOB":
            return
        nueva_ruta_procesos = {*procesos_en_ruta, objeto["id"]}
        for entrada in objeto.get("entradas", []):
            if not entrada.get("habilitada", True):
                continue
            destino, estado = resolver_llamada_entrada(entrada, inventario)
            if not destino or estado != "resuelta":
                continue
            if ids_permitidos is not None and destino["id"] not in ids_permitidos:
                continue
            recorrer(
                destino,
                ocurrencia_id,
                ruta_actual,
                (*posicion, entrada.get("indice", 0)),
                entrada,
                nueva_ruta_procesos,
            )

    objeto_raiz = obtener_objeto_inventario(arbol.get("tipo", ""), arbol.get("id", ""), inventario)
    if objeto_raiz and (ids_permitidos is None or objeto_raiz["id"] in ids_permitidos):
        recorrer(objeto_raiz, None, [], (0,), None, set())
    return ocurrencias


def crear_contexto_flujo(
    ocurrencia: dict[str, Any],
    grafo: dict[str, Any],
    nodos_incluidos: set[str],
    nodo_objetivo: str | None,
    tipo_contexto: str,
    parent_contexto_id: str | None = None,
) -> dict[str, Any]:
    nodos = []
    for nombre in grafo["orden_nodos"]:
        if nombre not in nodos_incluidos:
            continue
        datos = grafo["nodos"][nombre]
        nodos.append(
            {
                **datos,
                "nivel": grafo["niveles"].get(nombre),
                "es_objetivo": nombre == nodo_objetivo,
                "es_inicial": nombre in grafo["nodos_iniciales"],
            }
        )
    relaciones = [
        relacion
        for relacion in grafo["relaciones"]
        if relacion["origen"] in nodos_incluidos and relacion["destino"] in nodos_incluidos
    ]
    ciclos = [
        ciclo for ciclo in grafo["ciclos"] if any(nombre in nodos_incluidos for nombre in ciclo)
    ]
    return {
        "contexto_id": f"{tipo_contexto}::{ocurrencia['ocurrencia_id']}",
        "tipo_contexto": tipo_contexto,
        "parent_contexto_id": parent_contexto_id,
        "ocurrencia_id": ocurrencia["ocurrencia_id"],
        "proceso_id": ocurrencia["proceso_id"],
        "proceso": ocurrencia["proceso"],
        "tipo": ocurrencia["tipo"],
        "ruta": ocurrencia["ruta"],
        "ruta_ocurrencia": ocurrencia["ruta_ocurrencia"],
        "nodo_objetivo": nodo_objetivo,
        "nodos_iniciales": grafo["nodos_iniciales"],
        "nodos": nodos,
        "relaciones_precedencia": relaciones,
        "ciclos": ciclos,
        "avisos": list(grafo["avisos"]),
    }


def construir_flujo_preparacion(
    ocurrencia_objetivo_id: str,
    resultado: dict[str, Any],
    inventario: dict[str, Any],
) -> dict[str, Any]:
    ocurrencias = construir_ocurrencias_proceso(
        resultado["arbol"], inventario, set(resultado.get("nodos", {}))
    )
    por_id = {item["ocurrencia_id"]: item for item in ocurrencias}
    objetivo = por_id.get(ocurrencia_objetivo_id)
    if not objetivo:
        raise ValueError("No se encontró la ocurrencia seleccionada en la jerarquía reconstruida.")

    contextos_principales: list[dict[str, Any]] = []
    ciclos_detectados: list[dict[str, Any]] = []
    dependencias_recursivas: list[dict[str, Any]] = []
    avisos: list[str] = []

    if objetivo["parent_ocurrencia_id"] is None:
        objeto = obtener_objeto_inventario(objetivo["tipo"], objetivo["proceso_id"], inventario)
        grafo = construir_grafo_proceso(objeto, inventario) if objeto else None
        if grafo:
            contexto = crear_contexto_flujo(
                objetivo, grafo, set(grafo["orden_nodos"]), None, "flujo_general"
            )
            contextos_principales.append(contexto)
    else:
        ocurrencia_actual = objetivo
        while ocurrencia_actual.get("parent_ocurrencia_id") is not None:
            padre = por_id.get(ocurrencia_actual["parent_ocurrencia_id"])
            if not padre:
                avisos.append("[CONTEXTO PADRE NO ENCONTRADO]")
                break
            objeto_padre = obtener_objeto_inventario(padre["tipo"], padre["proceso_id"], inventario)
            if not objeto_padre:
                break
            grafo = construir_grafo_proceso(objeto_padre, inventario)
            nodo_llamada = ocurrencia_actual.get("entrada_padre", "")
            incluidos = obtener_predecesores_hasta_nodo(nodo_llamada, grafo["predecesores"])
            if not incluidos:
                avisos.append(
                    f"[NODO DE LLAMADA NO ENCONTRADO] {padre['proceso']} -> {ocurrencia_actual['proceso']}"
                )
            contexto = crear_contexto_flujo(
                padre, grafo, incluidos, nodo_llamada, "ruta_objetivo"
            )
            contextos_principales.append(contexto)
            ocurrencia_actual = padre
        contextos_principales.reverse()

    contextos = list(contextos_principales)
    ids_permitidos = set(resultado["nodos"])
    expandidas: set[str] = set()

    def expandir_contexto(
        contexto: dict[str, Any], procesos_en_expansion: set[str]
    ) -> None:
        if contexto["contexto_id"] in expandidas:
            return
        expandidas.add(contexto["contexto_id"])
        for nodo in contexto["nodos"]:
            referencia = nodo.get("proceso_referenciado")
            if not nodo.get("es_llamada_proceso") or not referencia or nodo.get("es_objetivo"):
                continue
            if referencia["id"] not in ids_permitidos:
                continue
            ruta_nueva = [*contexto["ruta_ocurrencia"], referencia["nombre"]]
            ocurrencia_id = f"{contexto['ocurrencia_id']}/exp-{nodo['indice']}"
            if referencia["id"] in procesos_en_expansion:
                dependencias_recursivas.append(
                    {
                        "proceso": referencia["nombre"],
                        "ruta_ocurrencia": ruta_nueva,
                        "mensaje": "[DEPENDENCIA RECURSIVA DETECTADA]",
                    }
                )
                continue
            objeto = obtener_objeto_inventario(referencia["tipo"], referencia["id"], inventario)
            if not objeto:
                continue
            ocurrencia_expandida = {
                "ocurrencia_id": ocurrencia_id,
                "proceso_id": referencia["id"],
                "proceso": referencia["nombre"],
                "tipo": referencia["tipo"],
                "ruta": referencia["ruta"],
                "ruta_ocurrencia": ruta_nueva,
            }
            grafo = construir_grafo_proceso(objeto, inventario)
            subcontexto = crear_contexto_flujo(
                ocurrencia_expandida,
                grafo,
                set(grafo["orden_nodos"]),
                None,
                "expansion_predecesor",
                contexto["contexto_id"],
            )
            contextos.append(subcontexto)
            expandir_contexto(subcontexto, {*procesos_en_expansion, referencia["id"]})

    for contexto in contextos_principales:
        expandir_contexto(contexto, {contexto["proceso_id"]})

    for contexto in contextos:
        for ciclo in contexto["ciclos"]:
            registro = {
                "proceso": contexto["proceso"],
                "ruta_ocurrencia": contexto["ruta_ocurrencia"],
                "nodos": ciclo,
                "relacion": " -> ".join(ciclo),
            }
            if registro not in ciclos_detectados:
                ciclos_detectados.append(registro)

    return {
        "objetivo": {
            "ocurrencia_id": objetivo["ocurrencia_id"],
            "proceso_id": objetivo["proceso_id"],
            "proceso": objetivo["proceso"],
            "tipo": objetivo["tipo"],
            "ruta": objetivo["ruta"],
            "ruta_ocurrencia": objetivo["ruta_ocurrencia"],
        },
        "contextos": contextos,
        "ciclos_detectados": ciclos_detectados,
        "dependencias_recursivas": dependencias_recursivas,
        "avisos": avisos,
    }


class ReconstructorPentahoApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Reconstrucción de procesos ETL Pentaho")
        self.root.geometry("1320x840")

        self.ruta_xml: Path | None = None
        self.rutas_scripts: list[Path] = []
        self.raiz_xml: ET.Element | None = None
        self.inventario: dict[str, Any] = {"jobs": {}, "transformaciones": {}, "carpetas": []}
        self.resultado_actual: dict[str, Any] | None = None
        self.job_ids_filtrados: dict[str, str] = {}
        self.diagrama_proceso_actual: tuple[str, str] | None = None
        self.diagrama_historial: list[tuple[str, str]] = []
        self.diagrama_job_raiz_id: str | None = None
        self.diagrama_targets: dict[str, dict[str, Any]] = {}
        self.flujo_preparacion_actual: dict[str, Any] | None = None
        self.flujo_ocurrencias_por_etiqueta: dict[str, dict[str, Any]] = {}
        self.historial_flujo_preparacion: list[dict[str, Any] | None] = []
        self.vista_proceso_flujo_actual: dict[str, Any] | None = None
        self.flujo_targets_visual: dict[str, dict[str, Any]] = {}
        self.localizacion_parametros_actual: dict[str, Any] | None = None
        self.localizacion_variables_actual: dict[str, Any] | None = None
        self.ruta_kettle: Path | None = None
        self.propiedades_kettle: dict[str, dict[str, Any]] = {}
        self.variables_kettle = self.propiedades_kettle
        self.indice_kettle_normalizado: dict[str, dict[str, Any]] = {}
        self.diagnostico_kettle: dict[str, Any] = {
            "lineas_leidas": 0,
            "propiedades_detectadas": 0,
            "codificacion": None,
            "primeras_claves": [],
            "ultima_busqueda_fallida_repr": None,
        }
        self.comparacion_kettle: dict[str, Any] = comparar_variables_con_kettle([], {})
        self.linaje_variable_actual: dict[str, Any] | None = None
        self.linaje_parametro_actual: dict[str, Any] | None = None
        self.parametros_linaje_por_etiqueta: dict[str, dict[str, str]] = {}

        self.nombre_archivo_var = tk.StringVar(value="Sin archivo seleccionado")
        self.ruta_archivo_var = tk.StringVar(value="")
        self.scripts_archivo_var = tk.StringVar(value="Sin scripts .sh cargados")
        self.carpeta_var = tk.StringVar()
        self.job_principal_var = tk.StringVar()
        self.diagrama_ruta_var = tk.StringVar(value="Sin proceso abierto")
        self.diagrama_estado_var = tk.StringVar(value="")
        self.flujo_objetivo_var = tk.StringVar(value="")
        self.parametro_localizar_var = tk.StringVar(value="")
        self.variable_localizar_var = tk.StringVar(value="")
        self.variable_linaje_var = tk.StringVar(value="")
        self.parametro_linaje_var = tk.StringVar(value="")
        self.kettle_archivo_var = tk.StringVar(value="Sin archivo cargado")
        self.kettle_estadisticas_var = tk.StringVar(
            value=(
                "Propiedades totales en Kettle: 0 | Variables del proceso: 0 | "
                "Coincidencias directas: 0 | Propiedades ajenas ignoradas: 0\n"
                "Creadas/renombradas: 0 | Derivadas de Kettle: 0 | "
                "Origen dinámico: 0 | Origen interno: 0 | Origen no resuelto: 0\n"
                "Lectura segura: 0 líneas | sin archivo | Primeras claves: -"
            )
        )
        self.resumen_vars = {
            "archivo": tk.StringVar(value="-"),
            "jobs": tk.StringVar(value="0"),
            "transformaciones": tk.StringVar(value="0"),
            "carpetas": tk.StringVar(value="0"),
            "carpeta": tk.StringVar(value="-"),
            "job": tk.StringVar(value="-"),
            "nodos": tk.StringVar(value="0"),
            "jobs_alcanzables": tk.StringVar(value="0"),
            "transformaciones_alcanzables": tk.StringVar(value="0"),
            "relaciones": tk.StringVar(value="0"),
            "no_resueltas": tk.StringVar(value="0"),
            "no_resueltas_directas": tk.StringVar(value="0"),
            "ambiguas": tk.StringVar(value="0"),
            "dinamicas": tk.StringVar(value="0"),
            "total_parametros": tk.StringVar(value="0"),
            "total_variables": tk.StringVar(value="0"),
            "total_variables_kettle_usadas": tk.StringVar(value="0"),
            "total_variables_internas": tk.StringVar(value="0"),
            "total_parametros_declarados": tk.StringVar(value="0"),
            "total_parametros_recibidos": tk.StringVar(value="0"),
            "total_parametros_enviados": tk.StringVar(value="0"),
            "total_parametros_desde_scripts": tk.StringVar(value="0"),
        }

        self.crear_interfaz()

    def crear_interfaz(self) -> None:
        marco_superior = ttk.Frame(self.root, padding=12)
        marco_superior.pack(fill="x")
        marco_superior.columnconfigure(1, weight=1)
        marco_superior.columnconfigure(3, weight=1)

        ttk.Button(
            marco_superior,
            text="Seleccionar XML del repositorio",
            command=self.seleccionar_xml,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)

        ttk.Label(marco_superior, text="Archivo:").grid(row=0, column=1, sticky="w")
        ttk.Label(marco_superior, textvariable=self.nombre_archivo_var).grid(row=0, column=2, sticky="w")
        ttk.Button(marco_superior, text="Analizar XML", command=self.analizar_xml).grid(
            row=0, column=4, sticky="e", padx=(8, 0)
        )

        ttk.Label(marco_superior, text="Ruta:").grid(row=1, column=1, sticky="w")
        ttk.Label(marco_superior, textvariable=self.ruta_archivo_var).grid(row=1, column=2, columnspan=3, sticky="w")

        ttk.Label(marco_superior, text="Carpeta / ruta:").grid(row=2, column=0, sticky="w", pady=(10, 4))
        self.combo_carpetas = ttk.Combobox(
            marco_superior, textvariable=self.carpeta_var, state="readonly", width=45
        )
        self.combo_carpetas.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(10, 4), padx=(0, 8))
        self.combo_carpetas.bind("<<ComboboxSelected>>", self.al_cambiar_carpeta)

        ttk.Label(marco_superior, text="Job principal:").grid(row=2, column=3, sticky="w", pady=(10, 4))
        self.combo_jobs = ttk.Combobox(
            marco_superior, textvariable=self.job_principal_var, state="readonly", width=42
        )
        self.combo_jobs.grid(row=2, column=4, sticky="ew", pady=(10, 4))

        ttk.Button(marco_superior, text="Reconstruir proceso", command=self.reconstruir).grid(
            row=3, column=3, sticky="e", padx=(8, 8), pady=(8, 0)
        )
        ttk.Button(marco_superior, text="Guardar JSON", command=self.guardar_json).grid(
            row=3, column=4, sticky="e", pady=(8, 0)
        )
        ttk.Button(
            marco_superior,
            text="Cargar scripts .sh",
            command=self.seleccionar_scripts,
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Label(
            marco_superior, textvariable=self.scripts_archivo_var
        ).grid(row=3, column=1, columnspan=2, sticky="w", pady=(8, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.tab_resumen = ttk.Frame(self.notebook, padding=12)
        self.tab_inventario = ttk.Frame(self.notebook, padding=12)
        self.tab_jobs = ttk.Frame(self.notebook, padding=12)
        self.tab_transformaciones = ttk.Frame(self.notebook, padding=12)
        self.tab_arbol = ttk.Frame(self.notebook, padding=12)
        self.tab_relaciones = ttk.Frame(self.notebook, padding=12)
        self.tab_no_resueltas = ttk.Frame(self.notebook, padding=12)
        self.tab_parametros = ttk.Frame(self.notebook, padding=12)
        self.tab_variables = ttk.Frame(self.notebook, padding=12)
        self.tab_localizar_parametros = ttk.Frame(self.notebook, padding=12)
        self.tab_localizar_variables = ttk.Frame(self.notebook, padding=12)
        self.tab_linaje_variables = ttk.Frame(self.notebook, padding=12)
        self.tab_linaje_parametros = ttk.Frame(self.notebook, padding=12)
        self.tab_flujo_preparacion = ttk.Frame(self.notebook, padding=12)
        self.tab_diagrama = ttk.Frame(self.notebook, padding=12)

        for titulo, tab in [
            ("Resumen", self.tab_resumen),
            ("Inventario", self.tab_inventario),
            ("Jobs encontrados", self.tab_jobs),
            ("Transformaciones encontradas", self.tab_transformaciones),
            ("Árbol del proceso", self.tab_arbol),
            ("Relaciones", self.tab_relaciones),
            ("Referencias no resueltas", self.tab_no_resueltas),
            ("Parámetros", self.tab_parametros),
            ("Variables", self.tab_variables),
            ("Localizar parámetros", self.tab_localizar_parametros),
            ("Localizar variables", self.tab_localizar_variables),
            ("Linaje de variables", self.tab_linaje_variables),
            ("Linaje de parámetros", self.tab_linaje_parametros),
            ("Flujo de preparación", self.tab_flujo_preparacion),
            ("Diagrama", self.tab_diagrama),
        ]:
            self.notebook.add(tab, text=titulo)

        self.crear_tab_resumen()
        self.tree_inventario = self.crear_tabla(
            self.tab_inventario,
            ("nombre", "tipo", "ruta", "identificador"),
            ("Nombre", "Tipo", "Ruta/carpeta", "Identificador interno"),
        )
        self.tree_jobs = self.crear_tabla(
            self.tab_jobs,
            ("nombre", "ruta", "entradas", "ref_jobs", "ref_trans"),
            ("Nombre del Job", "Ruta/carpeta", "Entradas", "Refs Jobs", "Refs Transformaciones"),
        )
        self.tree_transformaciones = self.crear_tabla(
            self.tab_transformaciones,
            ("nombre", "ruta", "estado"),
            ("Nombre de la transformación", "Ruta/carpeta", "Estado"),
        )
        self.tree_proceso = ttk.Treeview(self.tab_arbol, columns=("tipo", "ruta", "estado"), show="tree headings")
        self.tree_proceso.heading("#0", text="Nodo")
        self.tree_proceso.heading("tipo", text="Tipo")
        self.tree_proceso.heading("ruta", text="Ruta")
        self.tree_proceso.heading("estado", text="Estado")
        self.tree_proceso.pack(fill="both", expand=True)

        self.tree_relaciones = self.crear_tabla(
            self.tab_relaciones,
            ("origen", "tipo_origen", "destino", "tipo_destino", "entrada", "estado"),
            ("Origen", "Tipo origen", "Destino", "Tipo destino", "Entrada del Job", "Estado"),
        )
        self.tree_no_resueltas = self.crear_tabla(
            self.tab_no_resueltas,
            ("job", "entrada", "tipo", "referencia", "motivo"),
            ("Job origen", "Entrada", "Tipo esperado", "Referencia encontrada", "Motivo"),
        )

        self.crear_tab_parametros()
        self.crear_tab_variables()
        self.crear_tab_localizar_parametros()
        self.crear_tab_localizar_variables()
        self.crear_tab_linaje_variables()
        self.crear_tab_linaje_parametros()
        self.crear_tab_flujo_preparacion()
        self.crear_tab_diagrama()

    def crear_tab_resumen(self) -> None:
        etiquetas = [
            ("Archivo XML cargado", "archivo"),
            ("Catálogo global - Jobs", "jobs"),
            ("Catálogo global - transformaciones", "transformaciones"),
            ("Catálogo global - carpetas/rutas", "carpetas"),
            ("Carpeta seleccionada", "carpeta"),
            ("Job principal seleccionado", "job"),
            ("Total de nodos del proceso reconstruido", "nodos"),
            ("Jobs alcanzables", "jobs_alcanzables"),
            ("Transformaciones alcanzables", "transformaciones_alcanzables"),
            ("Total de relaciones encontradas", "relaciones"),
            ("Total de referencias sin resolver", "no_resueltas"),
            ("Referencias no resueltas directas", "no_resueltas_directas"),
            ("Referencias ambiguas", "ambiguas"),
            ("Referencias dinámicas", "dinamicas"),
            ("Total de parámetros", "total_parametros"),
            ("Total de variables", "total_variables"),
            ("Variables Kettle usadas", "total_variables_kettle_usadas"),
            ("Variables internas", "total_variables_internas"),
            ("Parámetros declarados", "total_parametros_declarados"),
            ("Parámetros recibidos", "total_parametros_recibidos"),
            ("Parámetros enviados", "total_parametros_enviados"),
            ("Parámetros asignados desde scripts", "total_parametros_desde_scripts"),
        ]
        for fila, (texto, clave) in enumerate(etiquetas):
            ttk.Label(self.tab_resumen, text=f"{texto}:").grid(row=fila, column=0, sticky="w", padx=(0, 12), pady=4)
            ttk.Label(self.tab_resumen, textvariable=self.resumen_vars[clave]).grid(row=fila, column=1, sticky="w", pady=4)

    def crear_tabla(
        self,
        parent: ttk.Frame,
        columnas: tuple[str, ...],
        headings: tuple[str, ...],
    ) -> ttk.Treeview:
        contenedor = ttk.Frame(parent)
        contenedor.pack(fill="both", expand=True)

        tree = ttk.Treeview(contenedor, columns=columnas, show="headings")
        vsb = ttk.Scrollbar(contenedor, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(contenedor, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        for columna, heading in zip(columnas, headings):
            tree.heading(columna, text=heading)
            tree.column(columna, width=180, anchor="w")

        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        contenedor.rowconfigure(0, weight=1)
        contenedor.columnconfigure(0, weight=1)
        return tree

    def crear_tab_diagrama(self) -> None:
        barra = ttk.Frame(self.tab_diagrama)
        barra.pack(fill="x", pady=(0, 8))

        ttk.Button(barra, text="Volver", command=self.volver_diagrama).pack(side="left", padx=(0, 6))
        ttk.Button(barra, text="Job principal", command=self.ir_a_job_principal_diagrama).pack(side="left", padx=(0, 12))
        ttk.Label(barra, textvariable=self.diagrama_ruta_var).pack(side="left", fill="x", expand=True)

        ttk.Label(self.tab_diagrama, textvariable=self.diagrama_estado_var).pack(fill="x", pady=(0, 8))

        contenedor = ttk.Frame(self.tab_diagrama)
        contenedor.pack(fill="both", expand=True)
        self.canvas_diagrama = tk.Canvas(contenedor, background="#f8f8f8")
        vsb = ttk.Scrollbar(contenedor, orient="vertical", command=self.canvas_diagrama.yview)
        hsb = ttk.Scrollbar(contenedor, orient="horizontal", command=self.canvas_diagrama.xview)
        self.canvas_diagrama.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.canvas_diagrama.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        contenedor.rowconfigure(0, weight=1)
        contenedor.columnconfigure(0, weight=1)
        self.canvas_diagrama.bind("<Double-Button-1>", self.al_doble_click_diagrama)

    def crear_tab_parametros(self) -> None:
        contenedor = ttk.Frame(self.tab_parametros)
        contenedor.pack(fill="both", expand=True)
        self.texto_parametros = tk.Text(contenedor, wrap="word", state="disabled", font=("Consolas", 10))
        vsb = ttk.Scrollbar(contenedor, orient="vertical", command=self.texto_parametros.yview)
        self.texto_parametros.configure(yscrollcommand=vsb.set)
        self.texto_parametros.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        contenedor.rowconfigure(0, weight=1)
        contenedor.columnconfigure(0, weight=1)
        self.actualizar_vista_parametros()

    def crear_tab_variables(self) -> None:
        contenedor = ttk.Frame(self.tab_variables)
        contenedor.pack(fill="both", expand=True)
        self.texto_variables = tk.Text(
            contenedor, wrap="word", state="disabled", font=("Consolas", 10)
        )
        vsb = ttk.Scrollbar(
            contenedor, orient="vertical", command=self.texto_variables.yview
        )
        self.texto_variables.configure(yscrollcommand=vsb.set)
        self.texto_variables.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        contenedor.rowconfigure(0, weight=1)
        contenedor.columnconfigure(0, weight=1)
        self.actualizar_vista_variables()

    def crear_tab_localizar_parametros(self) -> None:
        barra = ttk.Frame(self.tab_localizar_parametros)
        barra.pack(fill="x", pady=(0, 8))
        ttk.Label(barra, text="Parámetro:").pack(side="left", padx=(0, 8))
        self.combo_parametro_localizar = ttk.Combobox(
            barra,
            textvariable=self.parametro_localizar_var,
            state="readonly",
            width=55,
        )
        self.combo_parametro_localizar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.combo_parametro_localizar.bind(
            "<<ComboboxSelected>>", self.al_cambiar_parametro_localizar
        )
        ttk.Button(
            barra,
            text="Buscar apariciones",
            command=self.buscar_apariciones_parametro_seleccionado,
        ).pack(side="right")

        contenedor = ttk.Frame(self.tab_localizar_parametros)
        contenedor.pack(fill="both", expand=True)
        self.texto_localizacion_parametros = tk.Text(
            contenedor, wrap="word", state="disabled", font=("Consolas", 10)
        )
        vsb = ttk.Scrollbar(
            contenedor, orient="vertical", command=self.texto_localizacion_parametros.yview
        )
        self.texto_localizacion_parametros.configure(yscrollcommand=vsb.set)
        self.texto_localizacion_parametros.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        contenedor.rowconfigure(0, weight=1)
        contenedor.columnconfigure(0, weight=1)
        self.actualizar_vista_localizacion_parametros()

    def crear_tab_localizar_variables(self) -> None:
        barra = ttk.Frame(self.tab_localizar_variables)
        barra.pack(fill="x", pady=(0, 8))
        ttk.Label(barra, text="Variable:").pack(side="left", padx=(0, 8))
        self.combo_variable_localizar = ttk.Combobox(
            barra,
            textvariable=self.variable_localizar_var,
            state="readonly",
            width=55,
        )
        self.combo_variable_localizar.pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        self.combo_variable_localizar.bind(
            "<<ComboboxSelected>>", self.al_cambiar_variable_localizar
        )
        ttk.Button(
            barra,
            text="Buscar apariciones",
            command=self.buscar_apariciones_variable_seleccionada,
        ).pack(side="right")

        contenedor = ttk.Frame(self.tab_localizar_variables)
        contenedor.pack(fill="both", expand=True)
        self.texto_localizacion_variables = tk.Text(
            contenedor, wrap="word", state="disabled", font=("Consolas", 10)
        )
        vsb = ttk.Scrollbar(
            contenedor,
            orient="vertical",
            command=self.texto_localizacion_variables.yview,
        )
        self.texto_localizacion_variables.configure(yscrollcommand=vsb.set)
        self.texto_localizacion_variables.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        contenedor.rowconfigure(0, weight=1)
        contenedor.columnconfigure(0, weight=1)
        self.actualizar_vista_localizacion_variables()

    def crear_tab_linaje_variables(self) -> None:
        barra_archivo = ttk.Frame(self.tab_linaje_variables)
        barra_archivo.pack(fill="x", pady=(0, 6))
        ttk.Button(
            barra_archivo,
            text="Seleccionar kettle.properties",
            command=self.seleccionar_kettle_properties,
        ).pack(side="left", padx=(0, 10))
        ttk.Label(barra_archivo, text="Archivo:").pack(side="left")
        ttk.Label(barra_archivo, textvariable=self.kettle_archivo_var).pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        ttk.Label(
            self.tab_linaje_variables, textvariable=self.kettle_estadisticas_var
        ).pack(fill="x", pady=(0, 8))

        barra_variable = ttk.Frame(self.tab_linaje_variables)
        barra_variable.pack(fill="x", pady=(0, 8))
        ttk.Label(barra_variable, text="Variable del proceso:").pack(
            side="left", padx=(0, 8)
        )
        self.combo_variable_linaje = ttk.Combobox(
            barra_variable,
            textvariable=self.variable_linaje_var,
            state="readonly",
            width=55,
        )
        self.combo_variable_linaje.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.combo_variable_linaje.bind(
            "<<ComboboxSelected>>", lambda _event: self.limpiar_linaje_seleccionado()
        )
        ttk.Button(
            barra_variable, text="Reconstruir linaje", command=self.reconstruir_linaje_seleccionado
        ).pack(side="right")

        panel = ttk.Panedwindow(self.tab_linaje_variables, orient="vertical")
        panel.pack(fill="both", expand=True)
        marco_tabla = ttk.Frame(panel)
        marco_diagrama = ttk.Frame(panel)
        panel.add(marco_tabla, weight=3)
        panel.add(marco_diagrama, weight=2)

        self.tree_linaje = self.crear_tabla(
            marco_tabla,
            ("orden", "ruta", "evento", "proceso", "componente", "origen", "destino", "estado", "detalle"),
            ("#", "Ruta de origen", "Evento", "Proceso", "Componente", "Variable origen", "Variable destino", "Estado", "Detalle"),
        )
        contenedor_canvas = ttk.Frame(marco_diagrama)
        contenedor_canvas.pack(fill="both", expand=True, pady=(8, 0))
        self.canvas_linaje = tk.Canvas(contenedor_canvas, background="#f8fafc", height=230)
        vsb = ttk.Scrollbar(contenedor_canvas, orient="vertical", command=self.canvas_linaje.yview)
        hsb = ttk.Scrollbar(contenedor_canvas, orient="horizontal", command=self.canvas_linaje.xview)
        self.canvas_linaje.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.canvas_linaje.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        contenedor_canvas.rowconfigure(0, weight=1)
        contenedor_canvas.columnconfigure(0, weight=1)
        self.actualizar_vista_linaje()

    def crear_tab_linaje_parametros(self) -> None:
        barra = ttk.Frame(self.tab_linaje_parametros)
        barra.pack(fill="x", pady=(0, 8))
        ttk.Label(barra, text="Parámetro del proceso:").pack(
            side="left", padx=(0, 8)
        )
        self.combo_parametro_linaje = ttk.Combobox(
            barra,
            textvariable=self.parametro_linaje_var,
            state="readonly",
            width=55,
        )
        self.combo_parametro_linaje.pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        self.combo_parametro_linaje.bind(
            "<<ComboboxSelected>>", lambda _event: self.limpiar_linaje_parametro()
        )
        ttk.Button(
            barra,
            text="Reconstruir linaje",
            command=self.reconstruir_linaje_parametro_seleccionado,
        ).pack(side="right")

        panel = ttk.Panedwindow(self.tab_linaje_parametros, orient="vertical")
        panel.pack(fill="both", expand=True)
        marco_tabla = ttk.Frame(panel)
        marco_recorrido = ttk.Frame(panel)
        panel.add(marco_tabla, weight=3)
        panel.add(marco_recorrido, weight=1)
        self.tree_linaje_parametros = self.crear_tabla(
            marco_tabla,
            (
                "orden",
                "evento",
                "proceso",
                "componente",
                "origen",
                "tipo_origen",
                "destino",
                "estado",
                "detalle",
            ),
            (
                "#",
                "Evento",
                "Proceso",
                "Componente",
                "Origen",
                "Tipo origen",
                "Parámetro destino",
                "Estado",
                "Detalle",
            ),
        )
        self.texto_recorrido_parametros = tk.Text(
            marco_recorrido,
            wrap="word",
            state="disabled",
            height=8,
            font=("Consolas", 10),
        )
        self.texto_recorrido_parametros.pack(fill="both", expand=True, pady=(8, 0))
        self.actualizar_vista_linaje_parametros()

    def crear_tab_flujo_preparacion(self) -> None:
        barra = ttk.Frame(self.tab_flujo_preparacion)
        barra.pack(fill="x", pady=(0, 8))
        ttk.Button(
            barra, text="Inicio", command=self.inicio_flujo_preparacion
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            barra, text="Volver", command=self.volver_flujo_preparacion
        ).pack(side="left", padx=(0, 12))
        ttk.Label(barra, text="Proceso objetivo:").pack(side="left", padx=(0, 8))
        self.combo_flujo_objetivo = ttk.Combobox(
            barra,
            textvariable=self.flujo_objetivo_var,
            state="readonly",
            width=90,
        )
        self.combo_flujo_objetivo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.combo_flujo_objetivo.bind(
            "<<ComboboxSelected>>", self.al_cambiar_objetivo_flujo_preparacion
        )
        ttk.Button(
            barra,
            text="Reconstruir flujo",
            command=self.reconstruir_flujo_preparacion_seleccionado,
        ).pack(side="right")

        contenedor = ttk.Frame(self.tab_flujo_preparacion)
        contenedor.pack(fill="both", expand=True)
        self.texto_flujo_preparacion = tk.Text(
            contenedor, wrap="word", state="disabled", font=("Consolas", 10)
        )
        vsb = ttk.Scrollbar(contenedor, orient="vertical", command=self.texto_flujo_preparacion.yview)
        self.texto_flujo_preparacion.configure(yscrollcommand=vsb.set)
        self.texto_flujo_preparacion.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        contenedor.rowconfigure(0, weight=1)
        contenedor.columnconfigure(0, weight=1)
        self.actualizar_vista_flujo_preparacion()

    def seleccionar_xml(self) -> None:
        ruta = filedialog.askopenfilename(
            title="Seleccionar XML del repositorio",
            filetypes=[("Archivos XML", "*.xml"), ("Todos los archivos", "*.*")],
        )
        if not ruta:
            return
        self.ruta_xml = Path(ruta)
        self.nombre_archivo_var.set(self.ruta_xml.name)
        self.ruta_archivo_var.set(str(self.ruta_xml))
        self.resumen_vars["archivo"].set(self.ruta_xml.name)

    def seleccionar_scripts(self) -> None:
        rutas = filedialog.askopenfilenames(
            title="Seleccionar scripts de ejecución Pentaho",
            filetypes=[("Scripts Shell", "*.sh"), ("Todos los archivos", "*.*")],
        )
        if not rutas:
            return
        self.rutas_scripts = [Path(ruta) for ruta in rutas]
        nombres = ", ".join(ruta.name for ruta in self.rutas_scripts[:3])
        if len(self.rutas_scripts) > 3:
            nombres += f" y {len(self.rutas_scripts) - 3} más"
        self.scripts_archivo_var.set(
            f"{len(self.rutas_scripts)} script(s): {nombres}"
        )

    def analizar_xml(self) -> None:
        if not self.ruta_xml:
            messagebox.showwarning("XML requerido", "Primero seleccione un XML del repositorio.")
            return

        try:
            raiz, inventario = cargar_xml_repositorio(self.ruta_xml)
        except Exception as error:
            messagebox.showerror("Error al analizar XML", str(error))
            return

        self.raiz_xml = raiz
        self.inventario = inventario
        self.resultado_actual = None

        if not inventario["jobs"]:
            messagebox.showwarning(
                "Sin Jobs",
                "El XML se cargó, pero no se detectaron Jobs con las heurísticas actuales.",
            )

        carpetas = [ALL_FOLDERS_LABEL, *(inventario["carpetas"] or ["/"])]
        self.combo_carpetas["values"] = carpetas
        self.carpeta_var.set(ALL_FOLDERS_LABEL)
        self.actualizar_jobs_filtrados()
        self.llenar_resumen()
        self.llenar_tabla_inventario()
        self.llenar_tabla_jobs()
        self.llenar_tabla_transformaciones()
        self.limpiar_resultados_reconstruccion()

    def al_cambiar_carpeta(self, _event: tk.Event | None = None) -> None:
        self.actualizar_jobs_filtrados()
        self.llenar_resumen()

    def actualizar_jobs_filtrados(self) -> None:
        carpeta = self.carpeta_var.get().strip() or "/"
        jobs = filtrar_jobs_catalogo_por_carpeta(self.inventario, carpeta)

        self.job_ids_filtrados = {}
        for job in jobs:
            etiqueta_base = f"{job['nombre']}  [{job['ruta']}]"
            etiqueta = etiqueta_base
            if etiqueta in self.job_ids_filtrados:
                etiqueta = f"{etiqueta_base}  ({job['id']})"
            self.job_ids_filtrados[etiqueta] = job["id"]
        valores = list(self.job_ids_filtrados.keys())
        self.combo_jobs["values"] = valores
        self.job_principal_var.set(valores[0] if valores else "")

    def obtener_job_principal_id(self) -> str | None:
        etiqueta = self.job_principal_var.get().strip()
        return self.job_ids_filtrados.get(etiqueta)

    def reconstruir(self) -> None:
        if not self.raiz_xml:
            messagebox.showwarning("XML requerido", "Primero analice un XML del repositorio.")
            return

        job_id = self.obtener_job_principal_id()
        if not job_id:
            messagebox.showwarning("Job requerido", "Seleccione un Job principal antes de reconstruir.")
            return

        try:
            self.resultado_actual = reconstruir_proceso(
                job_id, self.inventario, self.rutas_scripts
            )
        except Exception as error:
            messagebox.showerror("Error al reconstruir", str(error))
            return

        self.diagrama_job_raiz_id = job_id
        self.diagrama_historial = []
        self.diagrama_proceso_actual = ("JOB", job_id)

        self.llenar_resumen()
        self.llenar_arbol_proceso()
        self.llenar_tabla_relaciones()
        self.llenar_tabla_referencias_no_resueltas()
        self.actualizar_vista_parametros()
        self.actualizar_vista_variables()
        self.actualizar_selector_localizacion_parametros()
        self.actualizar_selectores_variables()
        self.actualizar_comparacion_kettle()
        self.actualizar_vista_linaje()
        self.actualizar_vista_linaje_parametros()
        self.actualizar_objetivos_flujo_preparacion()
        self.dibujar_diagrama_proceso()

    def guardar_json(self) -> None:
        if not self.resultado_actual or not self.ruta_xml:
            messagebox.showwarning("Sin resultados", "Primero reconstruya un proceso para poder exportarlo.")
            return

        destino = filedialog.asksaveasfilename(
            title="Guardar resultado en JSON",
            defaultextension=".json",
            filetypes=[("Archivos JSON", "*.json")],
            initialfile="resultado_reconstruccion_pentaho.json",
        )
        if not destino:
            return

        job_id = self.resultado_actual["job_principal_id"]
        job = self.inventario["jobs"][job_id]
        salida = {
            "xml_origen": {
                "nombre": self.ruta_xml.name,
                "ruta": str(self.ruta_xml),
            },
            "proceso": {
                "job_principal": job["nombre"],
                "carpeta_seleccionada": self.carpeta_var.get().strip(),
            },
            "catalogo_global": self.resultado_actual.get(
                "catalogo_global", construir_resumen_catalogo(self.inventario)
            ),
            "proceso_reconstruido": self.resultado_actual.get(
                "proceso_reconstruido",
                construir_resumen_proceso(self.resultado_actual, self.inventario),
            ),
            "resumen": {
                "total_jobs_xml": len(self.inventario["jobs"]),
                "total_transformaciones_xml": len(self.inventario["transformaciones"]),
                "total_carpetas_xml": len(self.inventario["carpetas"]),
                "total_nodos_proceso": len(self.resultado_actual["nodos"]),
                "total_jobs_alcanzables": self.resultado_actual["proceso_reconstruido"][
                    "jobs_alcanzables"
                ],
                "total_transformaciones_alcanzables": self.resultado_actual[
                    "proceso_reconstruido"
                ]["transformaciones_alcanzables"],
                "total_relaciones": len(self.resultado_actual["relaciones"]),
                "total_referencias_no_resueltas": len(self.resultado_actual["referencias_no_resueltas"]),
                "total_referencias_ambiguas": self.resultado_actual["proceso_reconstruido"][
                    "referencias_ambiguas"
                ],
                "total_referencias_dinamicas": self.resultado_actual[
                    "proceso_reconstruido"
                ]["referencias_dinamicas"],
                "total_parametros": self.resultado_actual["proceso_reconstruido"].get(
                    "total_parametros", 0
                ),
                "total_variables": self.resultado_actual["proceso_reconstruido"].get(
                    "total_variables", 0
                ),
                "total_variables_kettle_usadas": self.resultado_actual[
                    "proceso_reconstruido"
                ].get("total_variables_kettle_usadas", 0),
                "total_variables_internas": self.resultado_actual[
                    "proceso_reconstruido"
                ].get("total_variables_internas", 0),
                "total_parametros_declarados": self.resultado_actual[
                    "proceso_reconstruido"
                ].get("total_parametros_declarados", 0),
                "total_parametros_recibidos": self.resultado_actual[
                    "proceso_reconstruido"
                ].get("total_parametros_recibidos", 0),
                "total_parametros_enviados": self.resultado_actual[
                    "proceso_reconstruido"
                ].get("total_parametros_enviados", 0),
                "total_parametros_desde_scripts": self.resultado_actual[
                    "proceso_reconstruido"
                ].get("total_parametros_desde_scripts", 0),
            },
            "nodos": list(self.resultado_actual["nodos"].values()),
            "relaciones": self.resultado_actual["relaciones"],
            "referencias_no_resueltas": self.resultado_actual["referencias_no_resueltas"],
            "arbol": self.resultado_actual["arbol"],
            "parametros": self.resultado_actual.get("parametros", crear_informacion_parametros_vacia()),
            "variables": self.resultado_actual.get(
                "variables", crear_informacion_variables_vacia()
            ),
            "scripts_ejecucion": self.resultado_actual.get(
                "scripts_ejecucion", crear_informacion_scripts_vacia()
            ),
            "flujo_preparacion": self.flujo_preparacion_actual
            or {
                "objetivo": None,
                "contextos": [],
                "ciclos_detectados": [],
                "dependencias_recursivas": [],
                "avisos": [],
            },
            "localizacion_parametros": self.localizacion_parametros_actual
            or {
                "parametro_seleccionado": None,
                "apariciones": [],
                "errores": [],
            },
            "localizacion_variables": getattr(
                self, "localizacion_variables_actual", None
            )
            or {
                "variable_seleccionada": None,
                "apariciones": [],
                "errores": [],
            },
            "kettle_properties": {
                "archivo": self.ruta_kettle.name if self.ruta_kettle else None,
                "ruta": str(self.ruta_kettle) if self.ruta_kettle else None,
                "diagnostico": self.diagnostico_kettle,
                "comparacion": self.comparacion_kettle,
            },
            "linaje_variable": self.linaje_variable_actual
            or {
                "variable_seleccionada": None,
                "origen": None,
                "tipo_origen": None,
                "origen_kettle": None,
                "origenes_kettle": [],
                "estado_origen": None,
                "resumen_origen": {
                    "origen": None,
                    "tipo": None,
                    "cantidad_rutas": 0,
                    "rutas_resueltas_kettle": 0,
                    "rutas_dinamicas": 0,
                    "rutas_internas": 0,
                    "rutas_parametros": 0,
                    "rutas_no_resueltas": 0,
                },
                "rutas_origen": [],
                "aliases": [],
                "campos_flujo": [],
                "procesos_involucrados": [],
                "variables_creadas_renombradas": {},
                "eventos": [],
                "no_resueltos": [],
                "avisos": [],
                "diagnostico_busquedas_kettle": {
                    "claves_no_resueltas_repr": [],
                    "primeras_claves_disponibles": [],
                },
            },
            "linaje_parametro": getattr(self, "linaje_parametro_actual", None)
            or crear_linaje_parametro_vacio(),
        }

        with open(destino, "w", encoding="utf-8") as archivo:
            json.dump(salida, archivo, ensure_ascii=False, indent=2)
        messagebox.showinfo("JSON guardado", f"Resultado exportado en:\n{destino}")

    def llenar_resumen(self) -> None:
        resumen_proceso = (
            self.resultado_actual.get("proceso_reconstruido", {})
            if self.resultado_actual
            else {}
        )
        self.resumen_vars["archivo"].set(self.ruta_xml.name if self.ruta_xml else "-")
        self.resumen_vars["jobs"].set(str(len(self.inventario["jobs"])))
        self.resumen_vars["transformaciones"].set(str(len(self.inventario["transformaciones"])))
        self.resumen_vars["carpetas"].set(str(len(self.inventario["carpetas"])))
        self.resumen_vars["carpeta"].set(self.carpeta_var.get().strip() or "-")
        self.resumen_vars["job"].set(self.job_principal_var.get().strip() or "-")
        self.resumen_vars["nodos"].set(str(len(self.resultado_actual["nodos"])) if self.resultado_actual else "0")
        self.resumen_vars["jobs_alcanzables"].set(
            str(resumen_proceso.get("jobs_alcanzables", 0))
        )
        self.resumen_vars["transformaciones_alcanzables"].set(
            str(resumen_proceso.get("transformaciones_alcanzables", 0))
        )
        self.resumen_vars["relaciones"].set(
            str(len(self.resultado_actual["relaciones"])) if self.resultado_actual else "0"
        )
        self.resumen_vars["no_resueltas"].set(
            str(len(self.resultado_actual["referencias_no_resueltas"])) if self.resultado_actual else "0"
        )
        self.resumen_vars["no_resueltas_directas"].set(
            str(resumen_proceso.get("referencias_no_resueltas", 0))
        )
        self.resumen_vars["ambiguas"].set(
            str(resumen_proceso.get("referencias_ambiguas", 0))
        )
        self.resumen_vars["dinamicas"].set(
            str(resumen_proceso.get("referencias_dinamicas", 0))
        )
        for clave in (
            "total_parametros",
            "total_variables",
            "total_variables_kettle_usadas",
            "total_variables_internas",
            "total_parametros_declarados",
            "total_parametros_recibidos",
            "total_parametros_enviados",
            "total_parametros_desde_scripts",
        ):
            self.resumen_vars[clave].set(str(resumen_proceso.get(clave, 0)))

    def limpiar_treeview(self, tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def llenar_tabla_inventario(self) -> None:
        self.limpiar_treeview(self.tree_inventario)
        objetos = [
            *self.inventario["jobs"].values(),
            *self.inventario["transformaciones"].values(),
        ]
        objetos.sort(key=lambda item: (item["ruta"], item["tipo"], item["nombre"].lower()))
        for obj in objetos:
            self.tree_inventario.insert(
                "",
                "end",
                values=(obj["nombre"], obj["tipo"], obj["ruta"], obj.get("identificador_interno", "")),
            )

    def llenar_tabla_jobs(self) -> None:
        self.limpiar_treeview(self.tree_jobs)
        jobs = sorted(self.inventario["jobs"].values(), key=lambda item: (item["ruta"], item["nombre"].lower()))
        for job in jobs:
            self.tree_jobs.insert(
                "",
                "end",
                values=(
                    job["nombre"],
                    job["ruta"],
                    len(job.get("entradas", [])),
                    job.get("referencias_jobs", 0),
                    job.get("referencias_transformaciones", 0),
                ),
            )

    def llenar_tabla_transformaciones(self) -> None:
        self.limpiar_treeview(self.tree_transformaciones)
        transformaciones = sorted(
            self.inventario["transformaciones"].values(), key=lambda item: (item["ruta"], item["nombre"].lower())
        )
        for trans in transformaciones:
            self.tree_transformaciones.insert("", "end", values=(trans["nombre"], trans["ruta"], "disponible"))

    def llenar_arbol_proceso(self) -> None:
        self.limpiar_treeview(self.tree_proceso)
        if not self.resultado_actual:
            return
        self.insertar_nodo_arbol("", self.resultado_actual["arbol"])

    def insertar_nodo_arbol(self, parent: str, nodo: dict[str, Any]) -> None:
        item = self.tree_proceso.insert(
            parent,
            "end",
            text=nodo["nombre"],
            values=(nodo.get("tipo", ""), nodo.get("ruta", ""), nodo.get("estado", "")),
        )
        for hijo in nodo.get("children", []):
            self.insertar_nodo_arbol(item, hijo)

    def llenar_tabla_relaciones(self) -> None:
        self.limpiar_treeview(self.tree_relaciones)
        if not self.resultado_actual:
            return
        for rel in self.resultado_actual["relaciones"]:
            self.tree_relaciones.insert(
                "",
                "end",
                values=(
                    rel["origen"],
                    rel["origen_tipo"],
                    rel["destino"],
                    rel["destino_tipo"],
                    rel["entrada"],
                    rel["estado"],
                ),
            )

    def llenar_tabla_referencias_no_resueltas(self) -> None:
        self.limpiar_treeview(self.tree_no_resueltas)
        if not self.resultado_actual:
            return
        for ref in self.resultado_actual["referencias_no_resueltas"]:
            self.tree_no_resueltas.insert(
                "",
                "end",
                values=(
                    ref["job_origen"],
                    ref["entrada"],
                    ref["tipo_esperado"],
                    ref["referencia_encontrada"],
                    ref["motivo"],
                ),
            )

    def actualizar_vista_parametros(self) -> None:
        if not hasattr(self, "texto_parametros"):
            return

        lineas: list[str] = []
        informacion = self.resultado_actual.get("parametros") if self.resultado_actual else None
        if not informacion:
            lineas.append("Todavía no hay un proceso reconstruido.")
        else:
            for indice, proceso in enumerate(informacion.get("procesos", [])):
                if indice:
                    lineas.extend(["", "-" * 70, ""])
                lineas.extend(
                    [
                        f"PROCESO: {proceso['nombre']}",
                        f"TIPO: {proceso['tipo']}",
                        f"RUTA: {proceso['ruta'] or '/'}",
                        "",
                        "PARÁMETROS DECLARADOS",
                        "",
                    ]
                )
                parametros = proceso.get("parametros_declarados", [])
                if parametros:
                    for parametro in parametros:
                        lineas.extend(
                            [
                                f"- {parametro['nombre']}",
                                f"    Valor por defecto: {parametro['valor_default']}",
                                f"    Descripción: {parametro['descripcion']}",
                                "",
                            ]
                        )
                else:
                    lineas.extend(["- No se detectaron parámetros declarados.", ""])

                lineas.extend(["PARÁMETROS RECIBIDOS EN LLAMADAS", ""])
                recibidos = proceso.get("parametros_recibidos", [])
                if recibidos:
                    for llamada in recibidos:
                        lineas.extend(
                            [
                                f"- Padre: {llamada.get('proceso_origen', '')}",
                                f"    Entry: {llamada.get('entrada', '')}",
                                f"    Parámetro destino: {llamada.get('parametro_destino', '')}",
                                f"    Valor configurado: {llamada.get('valor_configurado', '')}",
                                (
                                    "    Origen inmediato: "
                                    f"{llamada.get('origen_tipo', '')} "
                                    f"{llamada.get('origen', '')}"
                                ),
                                "",
                            ]
                        )
                else:
                    lineas.extend(["- No se recibieron parámetros.", ""])

                lineas.extend(["PARÁMETROS ASIGNADOS DESDE SCRIPTS", ""])
                recibidos_scripts = proceso.get("parametros_recibidos_scripts", [])
                if recibidos_scripts:
                    for asignacion in recibidos_scripts:
                        lineas.extend(
                            [
                                f"- Script: {asignacion.get('script', '')}",
                                f"    Línea: {asignacion.get('linea', '')}",
                                (
                                    "    Parámetro destino: "
                                    f"{asignacion.get('parametro_destino', '')}"
                                ),
                                (
                                    "    Origen estático: "
                                    f"{asignacion.get('origen_tipo', '')} "
                                    f"{asignacion.get('origen', '')}"
                                ),
                                "",
                            ]
                        )
                else:
                    lineas.extend(
                        ["- No se recibieron parámetros desde scripts.", ""]
                    )

                lineas.extend(["PARÁMETROS ENVIADOS A SUBPROCESOS", ""])
                enviados = proceso.get("parametros_enviados", [])
                if enviados:
                    for llamada in enviados:
                        lineas.extend(
                            [
                                f"- Entry: {llamada.get('entrada', '')}",
                                f"    Proceso destino: {llamada.get('proceso_destino', '')}",
                                f"    Parámetro destino: {llamada.get('parametro_destino', '')}",
                                f"    Valor configurado: {llamada.get('valor_configurado', '')}",
                                "",
                            ]
                        )
                else:
                    lineas.append("- No se enviaron parámetros a subprocesos.")

            lineas.extend(["", "=" * 70, "", "RESUMEN DE PARÁMETROS", ""])
            resumen = informacion.get("resumen", [])
            if not resumen:
                lineas.append("No se detectaron parámetros.")
            for item in resumen:
                lineas.extend([item["nombre"], "", "Declarado en:"])
                lineas.extend(
                    [f"- {nombre}" for nombre in item["declarado_en"]]
                    or ["- No declarado en la jerarquía reconstruida."]
                )
                lineas.extend(["", "Aparece en llamadas:"])
                lineas.extend(
                    [f"- {relacion}" for relacion in item["aparece_en_llamadas"]]
                    or ["- No aparece en configuraciones de llamadas detectadas."]
                )
                lineas.extend([""])

            if informacion.get("errores"):
                lineas.extend(["", "ERRORES PARCIALES DE ANÁLISIS", ""])
                lineas.extend(f"- {error}" for error in informacion["errores"])

        self.texto_parametros.configure(state="normal")
        self.texto_parametros.delete("1.0", "end")
        self.texto_parametros.insert("1.0", "\n".join(lineas))
        self.texto_parametros.configure(state="disabled")

    def actualizar_vista_variables(self) -> None:
        if not hasattr(self, "texto_variables"):
            return
        informacion = self.resultado_actual.get("variables") if self.resultado_actual else None
        lineas: list[str] = []
        if not informacion:
            lineas.append("Todavía no hay un proceso reconstruido.")
        else:
            indice_kettle = crear_indice_kettle_normalizado(self.propiedades_kettle)
            for indice, proceso in enumerate(informacion.get("procesos", [])):
                if indice:
                    lineas.extend(["", "-" * 70, ""])
                lineas.extend(
                    [
                        f"PROCESO: {proceso['nombre']}",
                        f"TIPO: {proceso['tipo']}",
                        f"RUTA: {proceso['ruta'] or '/'}",
                        "",
                    ]
                )
                nombres_proceso = {
                    referencia.get("nombre", "")
                    for referencia in proceso.get("referencias", [])
                }
                nombres_proceso.update(
                    referencia.get("nombre", "")
                    for referencia in proceso.get(
                        "referencias_sin_clasificar", []
                    )
                )
                nombres_proceso.update(
                    transicion.get("variable_origen", "")
                    for transicion in proceso.get("transiciones", [])
                    if transicion.get("origen_tipo") == "VARIABLE"
                )
                kettle_usadas = sorted(
                    nombre
                    for nombre in nombres_proceso
                    if nombre and buscar_variable_en_kettle(nombre, indice_kettle) is not None
                )
                secciones = [
                    ("VARIABLES KETTLE USADAS", kettle_usadas, "nombre"),
                    ("VARIABLES LEÍDAS CON GETVARIABLE", proceso.get("get_variables", []), "variable_origen"),
                    ("VARIABLES CREADAS O MODIFICADAS CON SETVARIABLE", proceso.get("set_variables", []), "variable_destino"),
                    ("VARIABLES INTERNAS", proceso.get("variables_internas", []), "variable_destino"),
                    ("REFERENCIAS ENCONTRADAS", proceso.get("referencias", []), "nombre"),
                    ("VARIABLES CREADAS O RENOMBRADAS", proceso.get("variables_creadas_renombradas", []), "variable_destino"),
                ]
                for titulo, elementos, clave in secciones:
                    lineas.extend([titulo, ""])
                    valores: list[str] = []
                    for elemento in elementos:
                        valor = elemento if isinstance(elemento, str) else elemento.get(clave, "")
                        if valor and valor not in valores:
                            valores.append(valor)
                    lineas.extend([f"- {valor}" for valor in valores] or ["- Ninguna."])
                    lineas.append("")
            if informacion.get("errores"):
                lineas.extend(["ERRORES PARCIALES DE ANÁLISIS", ""])
                lineas.extend(f"- {error}" for error in informacion["errores"])
        self.texto_variables.configure(state="normal")
        self.texto_variables.delete("1.0", "end")
        self.texto_variables.insert("1.0", "\n".join(lineas))
        self.texto_variables.configure(state="disabled")

    def actualizar_selector_localizacion_parametros(self) -> None:
        self.localizacion_parametros_actual = None
        self.linaje_parametro_actual = None
        informacion = self.resultado_actual.get("parametros") if self.resultado_actual else None
        valores = obtener_parametros_disponibles(informacion)
        self.combo_parametro_localizar["values"] = valores
        self.parametro_localizar_var.set(valores[0] if valores else "")
        if hasattr(self, "combo_parametro_linaje"):
            opciones = obtener_parametros_contextuales_disponibles(informacion)
            self.parametros_linaje_por_etiqueta = {
                opcion["etiqueta"]: opcion for opcion in opciones
            }
            etiquetas = list(self.parametros_linaje_por_etiqueta)
            self.combo_parametro_linaje["values"] = etiquetas
            self.parametro_linaje_var.set(etiquetas[0] if etiquetas else "")
        self.actualizar_vista_localizacion_parametros()
        self.actualizar_vista_linaje_parametros()

    def actualizar_selectores_variables(self) -> None:
        self.localizacion_variables_actual = None
        self.linaje_variable_actual = None
        informacion = self.resultado_actual.get("variables") if self.resultado_actual else None
        valores = obtener_variables_disponibles(informacion, self.propiedades_kettle)
        if hasattr(self, "combo_variable_localizar"):
            self.combo_variable_localizar["values"] = valores
            if self.variable_localizar_var.get() not in valores:
                self.variable_localizar_var.set(valores[0] if valores else "")
        if hasattr(self, "combo_variable_linaje"):
            self.combo_variable_linaje["values"] = valores
            if self.variable_linaje_var.get() not in valores:
                self.variable_linaje_var.set(valores[0] if valores else "")
        self.actualizar_vista_localizacion_variables()
        self.actualizar_vista_linaje()

    def al_cambiar_parametro_localizar(self, _event: tk.Event | None = None) -> None:
        self.localizacion_parametros_actual = None
        self.actualizar_vista_localizacion_parametros()

    def buscar_apariciones_parametro_seleccionado(self) -> None:
        if not self.resultado_actual:
            messagebox.showwarning(
                "Sin proceso reconstruido",
                "Primero reconstruya un Job principal.",
            )
            return
        parametro = self.parametro_localizar_var.get().strip()
        if not parametro:
            messagebox.showwarning(
                "Parámetro requerido",
                "Seleccione un parámetro para localizar.",
            )
            return
        try:
            self.localizacion_parametros_actual = localizar_parametro(
                parametro, self.resultado_actual, self.inventario
            )
        except Exception as error:
            self.localizacion_parametros_actual = {
                "parametro_seleccionado": parametro,
                "apariciones": [],
                "errores": [f"Error parcial al localizar el parámetro: {error}"],
            }
        self.actualizar_vista_localizacion_parametros()

    def actualizar_vista_localizacion_parametros(self) -> None:
        if not hasattr(self, "texto_localizacion_parametros"):
            return
        resultado = self.localizacion_parametros_actual
        lineas: list[str] = ["LOCALIZACIÓN DEL PARÁMETRO", ""]
        if not self.resultado_actual:
            lineas.append("Todavía no hay un proceso reconstruido.")
        elif not resultado:
            lineas.append(
                "Seleccione un parámetro y pulse «Buscar apariciones»."
            )
        else:
            apariciones = resultado.get("apariciones", [])
            lineas.extend(
                [
                    f"PARÁMETRO: {resultado.get('parametro_seleccionado', '')}",
                    f"APARICIONES ENCONTRADAS: {len(apariciones)}",
                    "",
                ]
            )
            if not apariciones:
                lineas.append(
                    "No se encontraron apariciones del parámetro seleccionado dentro de la jerarquía reconstruida."
                )
            for indice, aparicion in enumerate(apariciones, start=1):
                lineas.extend(
                    [
                        "=" * 72,
                        "",
                        f"[{indice}] {aparicion['tipo_aparicion']}",
                        "",
                        f"PROCESO: {aparicion['proceso']}",
                        f"TIPO: {aparicion['tipo_proceso']}",
                        f"RUTA: {aparicion.get('ruta', '') or '/'}",
                    ]
                )
                for ruta_ocurrencia in aparicion.get("rutas_ocurrencia", []):
                    lineas.append(
                        f"RUTA DE OCURRENCIA: {' > '.join(ruta_ocurrencia)}"
                    )

                tipo_aparicion = aparicion["tipo_aparicion"]
                if tipo_aparicion == "DECLARACION":
                    lineas.extend(
                        [
                            f"DECLARACIÓN DE PARÁMETRO: {aparicion['parametro']}",
                            f"VALOR POR DEFECTO: {aparicion.get('valor_default', '')}",
                            f"DESCRIPCIÓN: {aparicion.get('descripcion', '')}",
                            f"CONTEXTO XML: {aparicion.get('contexto_xml', '')}",
                        ]
                    )
                elif tipo_aparicion == "USO_PARAMETRO":
                    clase = aparicion.get("clase_componente")
                    if clase:
                        lineas.append(f"{clase}: {aparicion.get('componente', '')}")
                        lineas.append(
                            f"TIPO {clase}: {aparicion.get('tipo_componente', '')}"
                        )
                    lineas.extend(
                        [
                            f"REFERENCIA: {aparicion.get('referencia_original', '')}",
                            f"CONTEXTO XML: {aparicion.get('contexto_xml', '')}",
                            "CONTENIDO: "
                            + self.truncar_contenido_localizacion(
                                aparicion.get("contenido_original", "")
                            ),
                        ]
                    )
                elif tipo_aparicion in {
                    "PARAMETRO_ENVIADO",
                    "PARAMETRO_RECIBIDO",
                    "PARAMETRO_ORIGEN_LLAMADA",
                }:
                    lineas.extend(
                        [
                            f"ENTRY: {aparicion.get('componente', '')}",
                            f"TIPO ENTRY: {aparicion.get('tipo_componente', '')}",
                            f"PROCESO ORIGEN: {aparicion.get('proceso_origen', '')}",
                            f"PROCESO DESTINO: {aparicion.get('proceso_destino', '')}",
                            f"TIPO DESTINO: {aparicion.get('tipo_destino', '')}",
                            f"PARÁMETRO DESTINO: {aparicion.get('parametro_destino', '')}",
                            f"VALOR CONFIGURADO: {aparicion.get('valor_configurado', '')}",
                            (
                                "ORIGEN INMEDIATO: "
                                f"{aparicion.get('origen_tipo', '')} "
                                f"{aparicion.get('origen', '')}"
                            ),
                            f"CONTEXTO XML: {aparicion.get('contexto_xml', '')}",
                        ]
                    )
                lineas.append("")

            if resultado.get("errores"):
                lineas.extend(["=" * 72, "", "ERRORES PARCIALES", ""])
                lineas.extend(f"- {error}" for error in resultado["errores"])

        self.texto_localizacion_parametros.configure(state="normal")
        self.texto_localizacion_parametros.delete("1.0", "end")
        self.texto_localizacion_parametros.insert("1.0", "\n".join(lineas))
        self.texto_localizacion_parametros.configure(state="disabled")

    def al_cambiar_variable_localizar(self, _event: tk.Event | None = None) -> None:
        self.localizacion_variables_actual = None
        self.actualizar_vista_localizacion_variables()

    def buscar_apariciones_variable_seleccionada(self) -> None:
        if not self.resultado_actual:
            messagebox.showwarning(
                "Sin proceso reconstruido", "Primero reconstruya un Job principal."
            )
            return
        variable = self.variable_localizar_var.get().strip()
        if not variable:
            messagebox.showwarning("Variable requerida", "Seleccione una variable para localizar.")
            return
        try:
            self.localizacion_variables_actual = localizar_variable(
                variable,
                self.resultado_actual,
                self.inventario,
                self.propiedades_kettle,
            )
        except Exception as error:
            self.localizacion_variables_actual = {
                "variable_seleccionada": variable,
                "apariciones": [],
                "errores": [f"Error parcial al localizar la variable: {error}"],
            }
        self.actualizar_vista_localizacion_variables()

    def actualizar_vista_localizacion_variables(self) -> None:
        if not hasattr(self, "texto_localizacion_variables"):
            return
        resultado = self.localizacion_variables_actual
        lineas: list[str] = ["LOCALIZACIÓN DE LA VARIABLE", ""]
        if not self.resultado_actual:
            lineas.append("Todavía no hay un proceso reconstruido.")
        elif not resultado:
            lineas.append("Seleccione una variable y pulse «Buscar apariciones».")
        else:
            apariciones = resultado.get("apariciones", [])
            lineas.extend(
                [
                    f"VARIABLE: {resultado.get('variable_seleccionada', '')}",
                    f"APARICIONES ENCONTRADAS: {len(apariciones)}",
                    "",
                ]
            )
            if not apariciones:
                lineas.append(
                    "No se encontraron apariciones dentro del ETL seleccionado."
                )
            for indice, aparicion in enumerate(apariciones, start=1):
                lineas.extend(
                    [
                        "=" * 72,
                        "",
                        f"[{indice}] {aparicion.get('tipo_aparicion', '')}",
                        f"PROCESO: {aparicion.get('proceso', '')}",
                        f"TIPO: {aparicion.get('tipo_proceso', '')}",
                        f"RUTA: {aparicion.get('ruta', '') or '/'}",
                        f"COMPONENTE: {aparicion.get('componente', '') or '-'}",
                        f"TIPO COMPONENTE: {aparicion.get('tipo_componente', '') or '-'}",
                        f"CONTEXTO XML: {aparicion.get('contexto_xml', '') or '-'}",
                    ]
                )
                if aparicion.get("referencia_original"):
                    lineas.append(
                        f"REFERENCIA: {aparicion.get('referencia_original', '')}"
                    )
                if aparicion.get("variable_origen") or aparicion.get("variable_destino"):
                    lineas.append(
                        f"TRANSICIÓN: {aparicion.get('variable_origen', '')} → "
                        f"{aparicion.get('variable_destino', '')}"
                    )
                if aparicion.get("parametro_destino"):
                    lineas.append(
                        "ASIGNA PARÁMETRO: "
                        f"{aparicion.get('proceso_destino', '')}."
                        f"{aparicion.get('parametro_destino', '')}"
                    )
                if aparicion.get("valor_enmascarado"):
                    lineas.append(
                        f"VALOR KETTLE: {aparicion.get('valor_enmascarado', '')}"
                    )
                if aparicion.get("contenido_original"):
                    lineas.append(
                        "CONTENIDO: "
                        + self.truncar_contenido_localizacion(
                            aparicion.get("contenido_original", "")
                        )
                    )
                lineas.append("")
            if resultado.get("errores"):
                lineas.extend(["ERRORES PARCIALES", ""])
                lineas.extend(f"- {error}" for error in resultado["errores"])
        self.texto_localizacion_variables.configure(state="normal")
        self.texto_localizacion_variables.delete("1.0", "end")
        self.texto_localizacion_variables.insert("1.0", "\n".join(lineas))
        self.texto_localizacion_variables.configure(state="disabled")

    def truncar_contenido_localizacion(self, contenido: str, limite: int = 300) -> str:
        contenido = (contenido or "").strip()
        if len(contenido) <= limite:
            return contenido
        return contenido[: limite - 3] + "..."

    def seleccionar_kettle_properties(self) -> None:
        ruta = filedialog.askopenfilename(
            title="Seleccionar kettle.properties",
            filetypes=[
                ("Archivos de propiedades", "*.properties"),
                ("Archivos de texto", "*.txt"),
                ("Todos los archivos", "*.*"),
            ],
        )
        if not ruta:
            return
        diagnostico: dict[str, Any] = {}
        try:
            propiedades = cargar_kettle_properties(Path(ruta), diagnostico)
        except Exception as error:
            messagebox.showerror("Error al cargar propiedades", str(error))
            return
        self.ruta_kettle = Path(ruta)
        self.propiedades_kettle = propiedades
        self.variables_kettle = self.propiedades_kettle
        self.indice_kettle_normalizado = crear_indice_kettle_normalizado(propiedades)
        self.diagnostico_kettle = diagnostico
        self.kettle_archivo_var.set(self.ruta_kettle.name)
        self.linaje_variable_actual = None
        self.actualizar_comparacion_kettle()
        self.actualizar_vista_linaje()
        if not propiedades:
            messagebox.showwarning(
                "Sin propiedades activas",
                "El archivo se leyó correctamente, pero no contiene propiedades activas. "
                "Las líneas vacías y las que comienzan por # o ! son comentarios.",
            )

    def buscar_variable_en_kettle(self, nombre: str) -> dict[str, Any] | None:
        datos = buscar_variable_en_kettle(nombre, self.indice_kettle_normalizado)
        if datos is None:
            self.diagnostico_kettle["ultima_busqueda_fallida_repr"] = repr(
                normalizar_clave_kettle(nombre)
            )
        return datos

    def actualizar_comparacion_kettle(self) -> None:
        informacion = self.resultado_actual.get("variables") if self.resultado_actual else None
        if informacion is not None:
            aplicar_clasificacion_kettle_variables(
                informacion, self.propiedades_kettle
            )
        variables = obtener_variables_disponibles(
            informacion, self.propiedades_kettle
        )
        self.comparacion_kettle = comparar_variables_con_kettle(
            variables,
            self.propiedades_kettle,
            self.resultado_actual,
            self.inventario if self.resultado_actual is not None else None,
        )
        datos = self.comparacion_kettle
        fallidas = datos.get("busquedas_fallidas_repr", [])
        self.diagnostico_kettle["ultima_busqueda_fallida_repr"] = (
            fallidas[0] if fallidas else None
        )
        primeras_claves = ", ".join(self.diagnostico_kettle.get("primeras_claves", [])) or "-"
        self.kettle_estadisticas_var.set(
            "Propiedades totales en Kettle: {total_propiedades} | "
            "Variables del proceso: {total_variables_proceso} | "
            "Coincidencias directas: {total_coincidencias_directas} | "
            "Propiedades ajenas ignoradas: {total_propiedades_ignoradas}\n"
            "Creadas/renombradas: {total_variables_creadas_renombradas} | "
            "Derivadas de Kettle: {total_variables_derivadas_resueltas} | "
            "Origen dinámico: {total_origen_dinamico} | "
            "Origen parámetro: {total_origen_parametro} | "
            "Origen interno: {total_origen_interno} | "
            "Origen no resuelto: {total_origen_no_resuelto}\n"
            "Lectura segura: {lineas} líneas | {codificacion} | Primeras claves: {claves}".format(
                **datos,
                lineas=self.diagnostico_kettle.get("lineas_leidas", 0),
                codificacion=self.diagnostico_kettle.get("codificacion") or "sin archivo",
                claves=primeras_claves,
            )
        )
        if self.resultado_actual is not None:
            self.resultado_actual["total_variables"] = len(variables)
            self.resultado_actual["total_variables_kettle_usadas"] = len(
                datos.get("propiedades_kettle_usadas", {})
            )
            self.resultado_actual["proceso_reconstruido"] = construir_resumen_proceso(
                self.resultado_actual, self.inventario
            )
        self.actualizar_selectores_variables()
        self.actualizar_vista_variables()
        self.llenar_resumen()

    def limpiar_linaje_seleccionado(self) -> None:
        self.linaje_variable_actual = None
        self.actualizar_vista_linaje()

    def reconstruir_linaje_seleccionado(self) -> None:
        if not self.resultado_actual:
            messagebox.showwarning("Sin proceso reconstruido", "Primero reconstruya un Job principal.")
            return
        variable = self.variable_linaje_var.get().strip()
        if not variable:
            messagebox.showwarning(
                "Variable requerida", "Seleccione una variable recopilada del proceso."
            )
            return
        try:
            self.linaje_variable_actual = construir_linaje_variable(
                variable, self.resultado_actual, self.inventario, self.propiedades_kettle
            )
        except Exception as error:
            self.linaje_variable_actual = {
                "variable_seleccionada": variable,
                "origen": None,
                "tipo_origen": "error_parcial",
                "origen_kettle": None,
                "origenes_kettle": [],
                "estado_origen": "error_parcial",
                "resumen_origen": {
                    "origen": None,
                    "tipo": "error_parcial",
                    "cantidad_rutas": 0,
                    "rutas_resueltas_kettle": 0,
                    "rutas_dinamicas": 0,
                    "rutas_internas": 0,
                    "rutas_parametros": 0,
                    "rutas_no_resueltas": 0,
                },
                "rutas_origen": [],
                "aliases": [variable],
                "campos_flujo": [],
                "procesos_involucrados": [],
                "variables_creadas_renombradas": {},
                "eventos": [],
                "no_resueltos": [f"Error parcial al reconstruir el linaje: {error}"],
                "avisos": [],
                "diagnostico_busquedas_kettle": {
                    "claves_no_resueltas_repr": [repr(variable)],
                    "primeras_claves_disponibles": list(self.indice_kettle_normalizado)[:5],
                },
            }
        self.actualizar_vista_linaje()

    def actualizar_vista_linaje(self) -> None:
        if not hasattr(self, "tree_linaje"):
            return
        self.limpiar_treeview(self.tree_linaje)
        self.canvas_linaje.delete("all")
        resultado = self.linaje_variable_actual
        if not self.resultado_actual:
            self.canvas_linaje.create_text(
                30, 30, anchor="nw", text="Todavía no hay un proceso reconstruido."
            )
            return
        if not resultado:
            self.canvas_linaje.create_text(
                30,
                30,
                anchor="nw",
                text="Seleccione una variable del proceso y pulse «Reconstruir linaje».",
            )
            return
        for evento in resultado.get("eventos", []):
            self.tree_linaje.insert(
                "",
                "end",
                values=(
                    evento["orden"], evento.get("ruta_origen", "global"),
                    evento["tipo_evento"], evento["proceso"],
                    evento["componente"], evento["variable_origen"],
                    evento["variable_destino"], evento["estado"], evento["detalle"],
                ),
            )
        self.dibujar_diagrama_linaje(resultado)

    def dibujar_diagrama_linaje(self, resultado: dict[str, Any]) -> None:
        eventos = resultado.get("eventos", [])
        resumen = resultado.get("resumen_origen", {})
        self.canvas_linaje.create_text(
            35,
            20,
            anchor="nw",
            text=(
                f"Origen: {resumen.get('origen', '-')} | Tipo: {resumen.get('tipo', '-')} | "
                f"Rutas: {resumen.get('cantidad_rutas', 0)} | "
                f"Kettle: {resumen.get('rutas_resueltas_kettle', 0)} | "
                f"Sin resolver: {resumen.get('rutas_no_resueltas', 0)}"
            ),
            font=("Segoe UI", 9, "bold"),
        )

        orden_rutas = [
            ruta.get("ruta_origen", "") for ruta in resultado.get("rutas_origen", [])
        ]
        rutas_eventos: dict[str, list[dict[str, Any]]] = {
            ruta: [evento for evento in eventos if evento.get("ruta_origen") == ruta]
            for ruta in orden_rutas
        }
        globales = [
            evento
            for evento in eventos
            if evento.get("ruta_origen") not in set(orden_rutas)
        ]
        if globales:
            rutas_eventos["global"] = globales
            orden_rutas.append("global")

        ancho_caja, alto_caja, separacion = 230, 78, 55
        x_inicial = 155
        y_inicial = 65
        separacion_vertical = 125
        max_eventos_ruta = 1
        for indice_ruta, ruta in enumerate(orden_rutas):
            eventos_ruta = rutas_eventos.get(ruta, [])
            if not eventos_ruta:
                continue
            max_eventos_ruta = max(max_eventos_ruta, len(eventos_ruta))
            y = y_inicial + indice_ruta * separacion_vertical
            self.canvas_linaje.create_text(
                35,
                y + alto_caja // 2,
                anchor="w",
                width=110,
                text=ruta,
                font=("Segoe UI", 9, "bold"),
                fill="#334155",
            )
            posiciones_eventos: list[tuple[int, int]] = []
            for indice, evento in enumerate(eventos_ruta):
                x = x_inicial + indice * (ancho_caja + separacion)
                posiciones_eventos.append((x, y))
                no_resuelto = evento.get("estado") != "resuelto"
                es_kettle = evento.get("tipo_evento") == "ORIGEN_KETTLE"
                fill = "#dcfce7" if es_kettle else ("#fee2e2" if no_resuelto else "#e0f2fe")
                outline = "#15803d" if es_kettle else ("#b91c1c" if no_resuelto else "#0369a1")
                predecesores = [
                    indice_anterior
                    for indice_anterior, anterior in enumerate(eventos_ruta[:indice])
                    if anterior.get("nodo_destino") == evento.get("nodo_origen")
                    and anterior.get("tipo_evento") != "USO"
                ]
                if predecesores:
                    indice_anterior = predecesores[-1]
                    anterior_x, anterior_y = posiciones_eventos[indice_anterior]
                    self.canvas_linaje.create_line(
                        anterior_x + ancho_caja,
                        anterior_y + alto_caja // 2,
                        x,
                        y + alto_caja // 2,
                        arrow="last",
                        fill="#64748b",
                        width=2,
                    )
                self.canvas_linaje.create_rectangle(
                    x, y, x + ancho_caja, y + alto_caja,
                    fill=fill, outline=outline, width=2,
                )
                self.canvas_linaje.create_text(
                    x + 8, y + 8, anchor="nw", width=ancho_caja - 16,
                    text=f"{evento['orden']}. {evento['tipo_evento']}",
                    font=("Segoe UI", 9, "bold"),
                )
                self.canvas_linaje.create_text(
                    x + 8, y + 29, anchor="nw", width=ancho_caja - 16,
                    text=(
                        f"{evento['variable_origen']} → {evento['variable_destino']}\n"
                        f"{evento['proceso']}"
                    ),
                    font=("Segoe UI", 8),
                )

        no_resueltos = resultado.get("no_resueltos", [])
        avisos = resultado.get("avisos", [])
        y_mensajes = y_inicial + max(1, len(orden_rutas)) * separacion_vertical + 15
        if no_resueltos:
            self.canvas_linaje.create_text(
                35, y_mensajes, anchor="nw",
                text="NO RESUELTO ESTÁTICAMENTE\n" + "\n".join(
                    f"• {aviso}" for aviso in no_resueltos
                ),
                fill="#991b1b", width=1100, font=("Segoe UI", 9),
            )
            y_mensajes += 45 + len(no_resueltos) * 20
        if avisos:
            self.canvas_linaje.create_text(
                35, y_mensajes, anchor="nw",
                text="AVISOS\n" + "\n".join(f"• {aviso}" for aviso in avisos),
                fill="#92400e", width=1100, font=("Segoe UI", 9),
            )
        ancho = max(1000, x_inicial + max_eventos_ruta * (ancho_caja + separacion) + 50)
        alto = y_mensajes + 100 + min(len(avisos), 8) * 20
        self.canvas_linaje.configure(scrollregion=(0, 0, ancho, alto))

    def limpiar_linaje_parametro(self) -> None:
        self.linaje_parametro_actual = None
        self.actualizar_vista_linaje_parametros()

    def reconstruir_linaje_parametro_seleccionado(self) -> None:
        if not self.resultado_actual:
            messagebox.showwarning(
                "Sin proceso reconstruido", "Primero reconstruya un Job principal."
            )
            return
        etiqueta = self.parametro_linaje_var.get().strip()
        if not etiqueta:
            messagebox.showwarning(
                "Parámetro requerido", "Seleccione un parámetro recopilado del proceso."
            )
            return
        opcion = getattr(self, "parametros_linaje_por_etiqueta", {}).get(etiqueta)
        parametro = opcion.get("nombre", "") if opcion else etiqueta
        proceso_id = opcion.get("proceso_id") if opcion else None
        try:
            self.linaje_parametro_actual = construir_linaje_parametro(
                parametro,
                self.resultado_actual,
                self.inventario,
                proceso_id,
                self.propiedades_kettle,
            )
        except Exception as error:
            self.linaje_parametro_actual = {
                **crear_linaje_parametro_vacio(),
                "parametro_seleccionado": parametro,
                "estado_origen": "error_parcial",
                "no_resueltos": [
                    f"Error parcial al reconstruir el linaje: {error}"
                ],
            }
        self.actualizar_vista_linaje_parametros()

    def actualizar_vista_linaje_parametros(self) -> None:
        if not hasattr(self, "tree_linaje_parametros"):
            return
        self.limpiar_treeview(self.tree_linaje_parametros)
        resultado = self.linaje_parametro_actual
        lineas: list[str] = ["RECORRIDO DEL PARÁMETRO", ""]
        if not self.resultado_actual:
            lineas.append("Todavía no hay un proceso reconstruido.")
        elif not resultado:
            lineas.append(
                "Seleccione un parámetro del proceso y pulse «Reconstruir linaje»."
            )
        else:
            for evento in resultado.get("eventos", []):
                self.tree_linaje_parametros.insert(
                    "",
                    "end",
                    values=(
                        evento.get("orden", ""),
                        evento.get("tipo_evento", ""),
                        evento.get("proceso", ""),
                        evento.get("componente", ""),
                        evento.get("origen", ""),
                        evento.get("origen_tipo", ""),
                        evento.get("destino", ""),
                        evento.get("estado", ""),
                        evento.get("detalle", ""),
                    ),
                )
            lineas.extend(
                item.get("texto", "") for item in resultado.get("recorrido", [])
            )
            if resultado.get("no_resueltos"):
                lineas.extend(["", "NO RESUELTO", ""])
                lineas.extend(f"- {item}" for item in resultado["no_resueltos"])
            if resultado.get("avisos"):
                lineas.extend(["", "AVISOS", ""])
                lineas.extend(f"- {item}" for item in resultado["avisos"])
        self.texto_recorrido_parametros.configure(state="normal")
        self.texto_recorrido_parametros.delete("1.0", "end")
        self.texto_recorrido_parametros.insert("1.0", "\n".join(lineas))
        self.texto_recorrido_parametros.configure(state="disabled")

    def actualizar_objetivos_flujo_preparacion(self) -> None:
        self.flujo_preparacion_actual = None
        self.flujo_ocurrencias_por_etiqueta = {}
        self.historial_flujo_preparacion = []
        self.vista_proceso_flujo_actual = None
        if not self.resultado_actual:
            self.combo_flujo_objetivo["values"] = []
            self.flujo_objetivo_var.set("")
            self.actualizar_vista_flujo_preparacion()
            return

        try:
            ocurrencias = construir_ocurrencias_proceso(
                self.resultado_actual["arbol"],
                self.inventario,
                set(self.resultado_actual.get("nodos", {})),
            )
        except Exception as error:
            ocurrencias = []
            self.flujo_preparacion_actual = {
                "objetivo": None,
                "contextos": [],
                "ciclos_detectados": [],
                "dependencias_recursivas": [],
                "avisos": [f"No se pudieron construir las ocurrencias: {error}"],
            }

        for ocurrencia in ocurrencias:
            contexto = " > ".join(ocurrencia["ruta_ocurrencia"])
            etiqueta = (
                f"{ocurrencia['proceso']} [{ocurrencia['tipo']}] — {contexto} "
                f"(ocurrencia {ocurrencia['ocurrencia_id']})"
            )
            self.flujo_ocurrencias_por_etiqueta[etiqueta] = ocurrencia

        valores = list(self.flujo_ocurrencias_por_etiqueta)
        self.combo_flujo_objetivo["values"] = valores
        self.flujo_objetivo_var.set(valores[0] if valores else "")
        self.actualizar_vista_flujo_preparacion()

    def al_cambiar_objetivo_flujo_preparacion(self, _event: tk.Event | None = None) -> None:
        self.flujo_preparacion_actual = None
        self.historial_flujo_preparacion = []
        self.vista_proceso_flujo_actual = None
        self.actualizar_vista_flujo_preparacion()

    def reconstruir_flujo_preparacion_seleccionado(self) -> None:
        if not self.resultado_actual:
            messagebox.showwarning(
                "Sin proceso reconstruido",
                "Primero reconstruya un Job principal.",
            )
            return
        ocurrencia = self.flujo_ocurrencias_por_etiqueta.get(
            self.flujo_objetivo_var.get().strip()
        )
        if not ocurrencia:
            messagebox.showwarning(
                "Objetivo requerido",
                "Seleccione una ocurrencia de Job o transformación.",
            )
            return
        try:
            self.flujo_preparacion_actual = construir_flujo_preparacion(
                ocurrencia["ocurrencia_id"], self.resultado_actual, self.inventario
            )
        except Exception as error:
            self.flujo_preparacion_actual = {
                "objetivo": {
                    "proceso": ocurrencia["proceso"],
                    "tipo": ocurrencia["tipo"],
                    "ruta_ocurrencia": ocurrencia["ruta_ocurrencia"],
                },
                "contextos": [],
                "ciclos_detectados": [],
                "dependencias_recursivas": [],
                "avisos": [f"Error parcial al reconstruir el flujo: {error}"],
            }
        self.historial_flujo_preparacion = []
        self.vista_proceso_flujo_actual = None
        self.actualizar_vista_flujo_preparacion()

    def insertar_linea_flujo(
        self, texto: str = "", destino: dict[str, Any] | None = None
    ) -> None:
        inicio = self.texto_flujo_preparacion.index("end-1c")
        self.texto_flujo_preparacion.insert("end", f"{texto}\n")
        if destino is None:
            return
        fin = f"{inicio}+{len(texto)}c"
        tag = f"flujo_proceso_{len(self.flujo_targets_visual) + 1}"
        self.flujo_targets_visual[tag] = destino
        self.texto_flujo_preparacion.tag_add(tag, inicio, fin)
        self.texto_flujo_preparacion.tag_add("flujo_ampliable", inicio, fin)
        self.texto_flujo_preparacion.tag_bind(
            tag,
            "<Double-Button-1>",
            lambda _event, tag_actual=tag: self.abrir_proceso_flujo(tag_actual),
        )
        self.texto_flujo_preparacion.tag_bind(
            tag, "<Enter>", lambda _event: self.texto_flujo_preparacion.configure(cursor="hand2")
        )
        self.texto_flujo_preparacion.tag_bind(
            tag, "<Leave>", lambda _event: self.texto_flujo_preparacion.configure(cursor="xterm")
        )

    def obtener_destino_navegable_flujo(
        self, contexto: dict[str, Any], nodo: dict[str, Any]
    ) -> dict[str, Any] | None:
        referencia = nodo.get("proceso_referenciado")
        if not referencia or not self.flujo_preparacion_actual:
            return None
        ruta_destino = [*contexto["ruta_ocurrencia"], referencia["nombre"]]
        contextos = self.flujo_preparacion_actual.get("contextos", [])
        candidato = next(
            (
                item
                for item in contextos
                if item.get("parent_contexto_id") == contexto["contexto_id"]
                and item["proceso_id"] == referencia["id"]
                and item["ruta_ocurrencia"] == ruta_destino
            ),
            None,
        )
        if candidato is None:
            candidato = next(
                (
                    item
                    for item in contextos
                    if item["proceso_id"] == referencia["id"]
                    and item["ruta_ocurrencia"] == ruta_destino
                ),
                None,
            )
        return {
            "contexto_id": candidato["contexto_id"] if candidato else None,
            "proceso": referencia["nombre"],
            "tipo": referencia["tipo"],
            "ruta_ocurrencia": ruta_destino,
        }

    def abrir_proceso_flujo(self, tag: str) -> None:
        destino = self.flujo_targets_visual.get(tag)
        if not destino:
            return
        self.historial_flujo_preparacion.append(self.vista_proceso_flujo_actual)
        self.vista_proceso_flujo_actual = destino
        self.actualizar_vista_flujo_preparacion()

    def volver_flujo_preparacion(self) -> None:
        if not self.historial_flujo_preparacion:
            return
        self.vista_proceso_flujo_actual = self.historial_flujo_preparacion.pop()
        self.actualizar_vista_flujo_preparacion()

    def inicio_flujo_preparacion(self) -> None:
        if not self.flujo_preparacion_actual:
            return
        self.historial_flujo_preparacion = []
        self.vista_proceso_flujo_actual = None
        self.actualizar_vista_flujo_preparacion()

    def mostrar_nodos_contexto_flujo(
        self,
        contexto: dict[str, Any],
        margen: str,
        contexto_objetivo_id: str | None,
    ) -> None:
        niveles = sorted(
            {nodo["nivel"] for nodo in contexto["nodos"] if nodo["nivel"] is not None}
        )
        for nivel in niveles:
            self.insertar_linea_flujo(f"{margen}NIVEL {nivel}")
            nodos_nivel = [nodo for nodo in contexto["nodos"] if nodo["nivel"] == nivel]
            for indice, nodo in enumerate(nodos_nivel):
                rama = "└──" if indice == len(nodos_nivel) - 1 else "├──"
                referencia = nodo.get("proceso_referenciado")
                es_objetivo_real = bool(
                    nodo.get("es_objetivo")
                    and contexto["contexto_id"] == contexto_objetivo_id
                )
                es_ruta_objetivo = bool(nodo.get("es_objetivo") and not es_objetivo_real)
                destino = (
                    self.obtener_destino_navegable_flujo(contexto, nodo)
                    if referencia and not es_objetivo_real
                    else None
                )
                referencia_no_resuelta = nodo.get("estado_referencia") in {
                    "ambigua",
                    "referencia_no_resuelta",
                    "referencia_dinamica",
                }
                prefijo = "[+] " if referencia and not es_objetivo_real else ""
                if referencia_no_resuelta:
                    prefijo = "[!] "
                marcas: list[str] = []
                if nodo.get("es_inicial"):
                    marcas.append("INICIO")
                if es_objetivo_real and not referencia:
                    marcas.append("OBJETIVO")
                elif es_ruta_objetivo and not referencia:
                    marcas.append("RUTA AL OBJETIVO")
                if not nodo.get("habilitado", True):
                    marcas.append("DESHABILITADO")
                sufijo = f" [{' | '.join(marcas)}]" if marcas else ""
                if referencia:
                    self.insertar_linea_flujo(
                        f"{margen}{rama} {prefijo}{nodo['nombre']}{sufijo}", destino
                    )
                    self.insertar_linea_flujo(f"{margen}    {referencia['tipo']}")
                    self.insertar_linea_flujo(f"{margen}    -> {referencia['nombre']}")
                    if es_objetivo_real:
                        self.insertar_linea_flujo(f"{margen}    [OBJETIVO]")
                    elif es_ruta_objetivo:
                        self.insertar_linea_flujo(f"{margen}    [RUTA AL OBJETIVO]")
                else:
                    self.insertar_linea_flujo(
                        f"{margen}{rama} {prefijo}{nodo['nombre']} ({nodo['tipo']}){sufijo}"
                    )
            self.insertar_linea_flujo()

        sin_nivel = [nodo for nodo in contexto["nodos"] if nodo["nivel"] is None]
        if sin_nivel:
            self.insertar_linea_flujo(
                f"{margen}SIN NIVEL TOPOLÓGICO [CICLO O DEPENDENCIA CÍCLICA]"
            )
            for nodo in sin_nivel:
                referencia = nodo.get("proceso_referenciado")
                es_objetivo_real = bool(
                    nodo.get("es_objetivo")
                    and contexto["contexto_id"] == contexto_objetivo_id
                )
                es_ruta_objetivo = bool(nodo.get("es_objetivo") and not es_objetivo_real)
                destino = (
                    self.obtener_destino_navegable_flujo(contexto, nodo)
                    if referencia and not es_objetivo_real
                    else None
                )
                prefijo = "[+] " if referencia and not es_objetivo_real else ""
                marca = " [OBJETIVO]" if es_objetivo_real else ""
                if es_ruta_objetivo:
                    marca = " [RUTA AL OBJETIVO]"
                self.insertar_linea_flujo(
                    f"{margen}└── {prefijo}{nodo['nombre']} ({nodo['tipo']}){marca}", destino
                )
            self.insertar_linea_flujo()

        if not contexto["nodos"]:
            self.insertar_linea_flujo(
                f"{margen}Este proceso no contiene un flujo interno ampliable."
            )
            self.insertar_linea_flujo()

    def mostrar_relaciones_contextos_flujo(
        self, contextos: list[dict[str, Any]]
    ) -> None:
        self.insertar_linea_flujo("=" * 76)
        self.insertar_linea_flujo()
        self.insertar_linea_flujo("RELACIONES DE PRECEDENCIA")
        self.insertar_linea_flujo()
        hay_relaciones = False
        for contexto in contextos:
            for relacion in contexto.get("relaciones_precedencia", []):
                hay_relaciones = True
                self.insertar_linea_flujo(f"[{' > '.join(contexto['ruta_ocurrencia'])}]")
                self.insertar_linea_flujo(relacion["origen"])
                self.insertar_linea_flujo("    PRECEDE")
                self.insertar_linea_flujo(relacion["destino"])
                self.insertar_linea_flujo()
        if not hay_relaciones:
            self.insertar_linea_flujo("No se detectaron relaciones de precedencia aplicables.")

    def mostrar_vista_principal_flujo(self, flujo: dict[str, Any]) -> None:
        objetivo = flujo.get("objetivo")
        self.insertar_linea_flujo("FLUJO DE PREPARACIÓN")
        self.insertar_linea_flujo()
        if objetivo:
            self.insertar_linea_flujo(f"OBJETIVO: {objetivo['proceso']} [{objetivo['tipo']}]")
            self.insertar_linea_flujo(f"RUTA: {' > '.join(objetivo['ruta_ocurrencia'])}")
            self.insertar_linea_flujo()
        self.insertar_linea_flujo("=" * 76)
        self.insertar_linea_flujo()

        contextos = [
            contexto
            for contexto in flujo.get("contextos", [])
            if contexto.get("tipo_contexto") != "expansion_predecesor"
        ]
        contexto_objetivo_id = contextos[-1]["contexto_id"] if contextos else None
        for contexto in contextos:
            profundidad = max(0, len(contexto["ruta_ocurrencia"]) - 1)
            margen = "    " * profundidad
            self.insertar_linea_flujo(
                f"{margen}PROCESO: {contexto['proceso']} [{contexto['tipo']}]"
            )
            self.insertar_linea_flujo(
                f"{margen}CONTEXTO: {' > '.join(contexto['ruta_ocurrencia'])}"
            )
            self.insertar_linea_flujo()
            self.mostrar_nodos_contexto_flujo(contexto, margen, contexto_objetivo_id)
            for aviso in contexto.get("avisos", []):
                self.insertar_linea_flujo(f"{margen}{aviso}")
            if contexto.get("avisos"):
                self.insertar_linea_flujo()

        self.mostrar_relaciones_contextos_flujo(contextos)
        rutas_visibles = {tuple(contexto["ruta_ocurrencia"]) for contexto in contextos}
        ciclos = [
            ciclo
            for ciclo in flujo.get("ciclos_detectados", [])
            if tuple(ciclo["ruta_ocurrencia"]) in rutas_visibles
        ]
        if ciclos:
            self.insertar_linea_flujo()
            self.insertar_linea_flujo("CICLOS DETECTADOS")
            for ciclo in ciclos:
                self.insertar_linea_flujo(
                    f"[CICLO DETECTADO] {' > '.join(ciclo['ruta_ocurrencia'])}: {ciclo['relacion']}"
                )
        for aviso in flujo.get("avisos", []):
            self.insertar_linea_flujo(aviso)

    def mostrar_proceso_flujo(self, destino: dict[str, Any]) -> None:
        flujo = self.flujo_preparacion_actual or {}
        contexto = next(
            (
                item
                for item in flujo.get("contextos", [])
                if item["contexto_id"] == destino.get("contexto_id")
            ),
            None,
        )
        self.insertar_linea_flujo("FLUJO INTERNO DEL PROCESO")
        self.insertar_linea_flujo()
        self.insertar_linea_flujo(f"PROCESO: {destino['proceso']}")
        self.insertar_linea_flujo(f"TIPO: {destino['tipo']}")
        self.insertar_linea_flujo(f"CONTEXTO: {' > '.join(destino['ruta_ocurrencia'])}")
        self.insertar_linea_flujo()
        self.insertar_linea_flujo("=" * 76)
        self.insertar_linea_flujo()
        if contexto is None:
            self.insertar_linea_flujo("Este proceso no contiene un flujo interno ampliable.")
            return

        contextos_principales = [
            item
            for item in flujo.get("contextos", [])
            if item.get("tipo_contexto") != "expansion_predecesor"
        ]
        contexto_objetivo_id = (
            contextos_principales[-1]["contexto_id"] if contextos_principales else None
        )
        self.mostrar_nodos_contexto_flujo(contexto, "", contexto_objetivo_id)
        for aviso in contexto.get("avisos", []):
            self.insertar_linea_flujo(aviso)
        self.mostrar_relaciones_contextos_flujo([contexto])
        if contexto.get("ciclos"):
            self.insertar_linea_flujo()
            self.insertar_linea_flujo("CICLOS DETECTADOS")
            for ciclo in contexto["ciclos"]:
                self.insertar_linea_flujo(f"[CICLO DETECTADO] {' -> '.join(ciclo)}")

    def actualizar_vista_flujo_preparacion(self) -> None:
        if not hasattr(self, "texto_flujo_preparacion"):
            return
        self.texto_flujo_preparacion.configure(state="normal", cursor="xterm")
        self.texto_flujo_preparacion.delete("1.0", "end")
        self.texto_flujo_preparacion.tag_configure(
            "flujo_ampliable", foreground="#1d4ed8", underline=True
        )
        self.flujo_targets_visual = {}

        if not self.resultado_actual:
            self.insertar_linea_flujo("FLUJO DE PREPARACIÓN")
            self.insertar_linea_flujo()
            self.insertar_linea_flujo("Todavía no hay un proceso reconstruido.")
        elif not self.flujo_preparacion_actual:
            self.insertar_linea_flujo("FLUJO DE PREPARACIÓN")
            self.insertar_linea_flujo()
            self.insertar_linea_flujo(
                "Seleccione una ocurrencia como proceso objetivo y pulse «Reconstruir flujo»."
            )
        elif self.vista_proceso_flujo_actual:
            self.mostrar_proceso_flujo(self.vista_proceso_flujo_actual)
        else:
            self.mostrar_vista_principal_flujo(self.flujo_preparacion_actual)

        self.texto_flujo_preparacion.configure(state="disabled")

    def limpiar_resultados_reconstruccion(self) -> None:
        self.resultado_actual = None
        self.diagrama_proceso_actual = None
        self.diagrama_historial = []
        self.diagrama_job_raiz_id = None
        self.diagrama_targets = {}
        self.flujo_preparacion_actual = None
        self.flujo_ocurrencias_por_etiqueta = {}
        self.historial_flujo_preparacion = []
        self.vista_proceso_flujo_actual = None
        self.flujo_targets_visual = {}
        self.localizacion_parametros_actual = None
        self.localizacion_variables_actual = None
        self.linaje_variable_actual = None
        self.linaje_parametro_actual = None
        self.parametro_localizar_var.set("")
        self.combo_parametro_localizar["values"] = []
        self.variable_localizar_var.set("")
        self.combo_variable_localizar["values"] = []
        self.variable_linaje_var.set("")
        self.combo_variable_linaje["values"] = []
        self.parametro_linaje_var.set("")
        self.combo_parametro_linaje["values"] = []
        self.parametros_linaje_por_etiqueta = {}
        self.flujo_objetivo_var.set("")
        self.combo_flujo_objetivo["values"] = []
        self.diagrama_ruta_var.set("Sin proceso abierto")
        self.diagrama_estado_var.set("")
        self.llenar_resumen()
        self.llenar_arbol_proceso()
        self.llenar_tabla_relaciones()
        self.llenar_tabla_referencias_no_resueltas()
        self.actualizar_vista_parametros()
        self.actualizar_vista_variables()
        self.actualizar_vista_localizacion_parametros()
        self.actualizar_vista_localizacion_variables()
        self.actualizar_comparacion_kettle()
        self.actualizar_vista_linaje()
        self.actualizar_vista_linaje_parametros()
        self.actualizar_vista_flujo_preparacion()
        self.canvas_diagrama.delete("all")

    def dibujar_diagrama_proceso(self) -> None:
        self.canvas_diagrama.delete("all")
        if not self.resultado_actual:
            self.canvas_diagrama.create_text(40, 40, anchor="nw", text="Todavía no hay un proceso reconstruido.")
            return

        posiciones: dict[str, tuple[int, int]] = {}
        cajas: dict[str, tuple[int, int, int, int]] = {}
        contador_y = [40]
        self.calcular_layout_arbol(self.resultado_actual["arbol"], 40, contador_y, posiciones)

        for nodo in self.iterar_nodos_arbol(self.resultado_actual["arbol"]):
            x, y = posiciones[nodo["tree_id"]]
            color = "#dbeafe" if nodo["tipo"] == "JOB" else "#dcfce7"
            borde = "#1d4ed8" if nodo["tipo"] == "JOB" else "#15803d"
            if nodo.get("estado") in {
                "referencia_no_resuelta",
                "ambigua",
                "referencia_dinamica",
            }:
                color = "#fee2e2"
                borde = "#b91c1c"
            if nodo.get("estado") == "ciclo_detectado":
                color = "#fef3c7"
                borde = "#b45309"
            ancho = 220
            alto = 54
            self.canvas_diagrama.create_rectangle(x, y, x + ancho, y + alto, fill=color, outline=borde, width=2)
            self.canvas_diagrama.create_text(
                x + 10,
                y + 12,
                anchor="nw",
                text=f"{nodo['tipo']}: {nodo['nombre']}",
                width=200,
                font=("Segoe UI", 9, "bold"),
            )
            self.canvas_diagrama.create_text(
                x + 10,
                y + 32,
                anchor="nw",
                text=nodo.get("ruta", "") or "/",
                width=200,
                font=("Segoe UI", 8),
            )
            cajas[nodo["tree_id"]] = (x, y, x + ancho, y + alto)

        for padre, hijo in self.iterar_aristas_arbol(self.resultado_actual["arbol"]):
            x1, y1, x2, y2 = cajas[padre["tree_id"]]
            hx1, hy1, _, hy2 = cajas[hijo["tree_id"]]
            color = "#6b7280"
            dash = None
            if hijo.get("estado") in {
                "referencia_no_resuelta",
                "ambigua",
                "referencia_dinamica",
            }:
                color = "#b91c1c"
                dash = (5, 4)
            elif hijo.get("estado") == "ciclo_detectado":
                color = "#b45309"
                dash = (3, 3)
            self.canvas_diagrama.create_line(
                x2,
                (y1 + y2) // 2,
                hx1,
                (hy1 + hy2) // 2,
                arrow="last",
                fill=color,
                width=2,
                dash=dash,
            )

        ancho_total = max((caja[2] for caja in cajas.values()), default=800) + 80
        alto_total = max((caja[3] for caja in cajas.values()), default=600) + 80
        self.canvas_diagrama.configure(scrollregion=(0, 0, ancho_total, alto_total))

    def calcular_layout_arbol(
        self,
        nodo: dict[str, Any],
        x: int,
        contador_y: list[int],
        posiciones: dict[str, tuple[int, int]],
    ) -> int:
        hijos = nodo.get("children", [])
        if not hijos:
            y = contador_y[0]
            posiciones[nodo["tree_id"]] = (x, y)
            contador_y[0] += 110
            return y

        y_hijos = [self.calcular_layout_arbol(hijo, x + 280, contador_y, posiciones) for hijo in hijos]
        y_actual = int(sum(y_hijos) / len(y_hijos))
        posiciones[nodo["tree_id"]] = (x, y_actual)
        return y_actual

    def iterar_nodos_arbol(self, nodo: dict[str, Any]) -> list[dict[str, Any]]:
        nodos = [nodo]
        for hijo in nodo.get("children", []):
            nodos.extend(self.iterar_nodos_arbol(hijo))
        return nodos

    def iterar_aristas_arbol(self, nodo: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        aristas: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for hijo in nodo.get("children", []):
            aristas.append((nodo, hijo))
            aristas.extend(self.iterar_aristas_arbol(hijo))
        return aristas

    def dibujar_diagrama_proceso(self) -> None:
        self.canvas_diagrama.delete("all")
        self.diagrama_targets = {}
        if not self.resultado_actual or not self.diagrama_proceso_actual:
            self.diagrama_ruta_var.set("Sin proceso abierto")
            self.diagrama_estado_var.set("Todavia no hay un proceso reconstruido.")
            self.canvas_diagrama.create_text(40, 40, anchor="nw", text="Todavia no hay un proceso reconstruido.")
            return

        tipo, proceso_id = self.diagrama_proceso_actual
        self.actualizar_ruta_diagrama()
        if tipo == "JOB":
            self.dibujar_job_actual(proceso_id)
        elif tipo == "TRANSFORMATION":
            self.dibujar_transformacion_actual(proceso_id)

    def abrir_proceso_diagrama(self, tipo: str, proceso_id: str, guardar_historial: bool = True) -> None:
        if self.diagrama_proceso_actual and guardar_historial:
            self.diagrama_historial.append(self.diagrama_proceso_actual)
        self.diagrama_proceso_actual = (tipo, proceso_id)
        self.dibujar_diagrama_proceso()

    def volver_diagrama(self) -> None:
        if not self.diagrama_historial:
            return
        self.diagrama_proceso_actual = self.diagrama_historial.pop()
        self.dibujar_diagrama_proceso()

    def ir_a_job_principal_diagrama(self) -> None:
        if not self.diagrama_job_raiz_id:
            return
        self.diagrama_historial = []
        self.diagrama_proceso_actual = ("JOB", self.diagrama_job_raiz_id)
        self.dibujar_diagrama_proceso()

    def al_doble_click_diagrama(self, event: tk.Event) -> None:
        item = self.canvas_diagrama.find_withtag("current")
        if not item:
            return
        for tag in self.canvas_diagrama.gettags(item[0]):
            if not tag.startswith("target::"):
                continue
            target = self.diagrama_targets.get(tag.split("::", 1)[1])
            if target:
                self.abrir_proceso_diagrama(target["tipo"], target["id"])
            return

    def actualizar_ruta_diagrama(self) -> None:
        partes: list[str] = []
        procesos = list(self.diagrama_historial)
        if self.diagrama_proceso_actual:
            procesos.append(self.diagrama_proceso_actual)
        for tipo, proceso_id in procesos:
            objeto = self.obtener_objeto_diagrama(tipo, proceso_id)
            if objeto:
                partes.append(objeto["nombre"])
        self.diagrama_ruta_var.set(" > ".join(partes) if partes else "Sin proceso abierto")

    def obtener_objeto_diagrama(self, tipo: str, proceso_id: str) -> dict[str, Any] | None:
        if tipo == "JOB":
            return self.inventario["jobs"].get(proceso_id)
        if tipo == "TRANSFORMATION":
            return self.inventario["transformaciones"].get(proceso_id)
        return None

    def dibujar_job_actual(self, job_id: str) -> None:
        job = self.inventario["jobs"].get(job_id)
        if not job:
            self.diagrama_estado_var.set("No se encontro el Job seleccionado en el inventario.")
            return

        elementos = [self.construir_elemento_diagrama_desde_entrada(entrada) for entrada in job.get("entradas", [])]
        self.dibujar_elementos_y_hops(
            titulo=f"JOB: {job['nombre']}",
            subtitulo=job.get("ruta", ""),
            elementos=elementos,
            hops=extraer_hops_job(job["xml_element"]),
        )

    def dibujar_transformacion_actual(self, trans_id: str) -> None:
        transformacion = self.inventario["transformaciones"].get(trans_id)
        if not transformacion:
            self.diagrama_estado_var.set("No se encontro la transformacion seleccionada en el inventario.")
            return

        pasos = extraer_pasos_transformacion(transformacion["xml_element"])
        elementos = [
            {
                "nombre": paso["nombre"],
                "tipo": paso.get("tipo_entrada") or "STEP",
                "x": paso.get("posicion_x", 0),
                "y": paso.get("posicion_y", 0),
                "draw": paso.get("draw", ""),
                "target": None,
                "estado": "",
                "tipo_esperado": "STEP",
            }
            for paso in pasos
        ]
        self.dibujar_elementos_y_hops(
            titulo=f"TRANSFORMATION: {transformacion['nombre']}",
            subtitulo=transformacion.get("ruta", ""),
            elementos=elementos,
            hops=extraer_hops_transformacion(transformacion["xml_element"]),
        )

    def construir_elemento_diagrama_desde_entrada(self, entrada: dict[str, Any]) -> dict[str, Any]:
        target: dict[str, Any] | None = None
        estado = ""
        tipo_esperado = entrada.get("tipo_esperado", "UNKNOWN")

        if not entrada.get("habilitada", True):
            busqueda = {"estado": "deshabilitada", "coincidencias": [], "motivo": ""}
            estado = "deshabilitada"
        elif tipo_esperado == "JOB":
            busqueda = buscar_job_referenciado(entrada_a_referencia(entrada), self.inventario)
        elif tipo_esperado == "TRANSFORMATION":
            busqueda = buscar_transformacion_referenciada(entrada_a_referencia(entrada), self.inventario)
        else:
            busqueda = {"estado": "", "coincidencias": [], "motivo": ""}

        if busqueda.get("estado") == "resuelta":
            destino = busqueda["coincidencias"][0]
            ids_permitidos = set((self.resultado_actual or {}).get("nodos", {}))
            if destino["id"] in ids_permitidos:
                target = {"tipo": destino["tipo"], "id": destino["id"], "nombre": destino["nombre"]}
                estado = "expandible"
            else:
                estado = "fuera_subgrafo"
        elif tipo_esperado in {"JOB", "TRANSFORMATION"} and not estado:
            estado = busqueda.get("estado", "referencia_no_resuelta")

        return {
            "nombre": entrada.get("nombre", ""),
            "tipo": entrada.get("tipo_entrada") or "ENTRY",
            "x": entrada.get("posicion_x", 0),
            "y": entrada.get("posicion_y", 0),
            "draw": entrada.get("draw", ""),
            "target": target,
            "estado": estado,
            "tipo_esperado": tipo_esperado,
        }

    def dibujar_elementos_y_hops(
        self,
        titulo: str,
        subtitulo: str,
        elementos: list[dict[str, Any]],
        hops: list[dict[str, str]],
    ) -> None:
        self.canvas_diagrama.create_text(30, 24, anchor="nw", text=titulo, font=("Segoe UI", 12, "bold"))
        self.canvas_diagrama.create_text(30, 48, anchor="nw", text=subtitulo or "/", font=("Segoe UI", 9))

        if not elementos:
            self.diagrama_estado_var.set("El proceso actual no contiene elementos internos para dibujar.")
            self.canvas_diagrama.create_text(30, 90, anchor="nw", text="No hay elementos internos en este proceso.")
            self.canvas_diagrama.configure(scrollregion=(0, 0, 900, 500))
            return

        posiciones = self.calcular_posiciones_diagrama(elementos)
        cajas: dict[str, tuple[int, int, int, int]] = {}

        for hop in hops:
            origen = hop.get("origen", "")
            destino = hop.get("destino", "")
            if origen not in posiciones or destino not in posiciones:
                continue
            x1, y1 = posiciones[origen]
            x2, y2 = posiciones[destino]
            habilitado = not es_falso(hop.get("habilitado", "Y"))
            self.canvas_diagrama.create_line(
                x1 + 150,
                y1 + 34,
                x2,
                y2 + 34,
                arrow="last",
                fill="#94a3b8" if habilitado else "#cbd5e1",
                width=2,
                dash=None if habilitado else (4, 3),
            )

        expandibles = 0
        for indice, elemento in enumerate(elementos, start=1):
            nombre = elemento["nombre"]
            x, y = posiciones[nombre]
            target = elemento.get("target")
            estado = elemento.get("estado", "")
            tags = ("nodo_diagrama",)
            if target:
                key = f"target_{indice}"
                self.diagrama_targets[key] = target
                tags = ("nodo_diagrama", f"target::{key}")
                expandibles += 1

            fill, outline = self.colores_elemento_diagrama(elemento)
            if es_falso(elemento.get("draw", "")):
                fill = "#f8fafc"
                outline = "#cbd5e1"

            self.canvas_diagrama.create_rectangle(x, y, x + 150, y + 68, fill=fill, outline=outline, width=2, tags=tags)
            self.canvas_diagrama.create_text(
                x + 10,
                y + 10,
                anchor="nw",
                text=nombre,
                width=130,
                font=("Segoe UI", 9, "bold"),
                tags=tags,
            )
            self.canvas_diagrama.create_text(
                x + 10,
                y + 42,
                anchor="nw",
                text=self.etiqueta_estado_elemento(elemento),
                width=130,
                font=("Segoe UI", 8),
                fill="#475569",
                tags=tags,
            )
            if target:
                self.canvas_diagrama.create_text(x + 136, y + 8, text="+", font=("Segoe UI", 12, "bold"), tags=tags)
            elif estado in {"referencia_no_resuelta", "ambigua", "referencia_dinamica"}:
                self.canvas_diagrama.create_text(x + 136, y + 8, text="!", font=("Segoe UI", 12, "bold"), fill="#b91c1c")
            cajas[nombre] = (x, y, x + 150, y + 68)

        ancho_total = max((caja[2] for caja in cajas.values()), default=900) + 120
        alto_total = max((caja[3] for caja in cajas.values()), default=500) + 120
        self.canvas_diagrama.configure(scrollregion=(0, 0, ancho_total, alto_total))

        if expandibles:
            self.diagrama_estado_var.set(
                f"{len(elementos)} elementos internos. {expandibles} elemento(s) abren otro proceso con doble clic."
            )
        else:
            self.diagrama_estado_var.set(
                f"{len(elementos)} elementos internos. Este proceso no utiliza otros Jobs ni transformaciones detectadas."
            )

    def calcular_posiciones_diagrama(self, elementos: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
        con_coordenadas = [elemento for elemento in elementos if elemento.get("x") or elemento.get("y")]
        posiciones: dict[str, tuple[int, int]] = {}
        if con_coordenadas:
            min_x = min(elemento.get("x", 0) for elemento in con_coordenadas)
            min_y = min(elemento.get("y", 0) for elemento in con_coordenadas)
            for indice, elemento in enumerate(elementos):
                if elemento.get("x") or elemento.get("y"):
                    x = elemento.get("x", 0) - min_x + 60
                    y = elemento.get("y", 0) - min_y + 100
                else:
                    x = 60 + (indice % 4) * 220
                    y = 100 + (indice // 4) * 130
                posiciones[elemento["nombre"]] = (x, y)
            return posiciones

        for indice, elemento in enumerate(elementos):
            posiciones[elemento["nombre"]] = (60 + (indice % 4) * 220, 100 + (indice // 4) * 130)
        return posiciones

    def colores_elemento_diagrama(self, elemento: dict[str, Any]) -> tuple[str, str]:
        if elemento.get("estado") == "expandible":
            if elemento.get("tipo_esperado") == "JOB":
                return "#dbeafe", "#1d4ed8"
            return "#dcfce7", "#15803d"
        if elemento.get("estado") in {
            "referencia_no_resuelta",
            "ambigua",
            "referencia_dinamica",
        }:
            return "#fee2e2", "#b91c1c"
        if elemento.get("estado") in {"deshabilitada", "fuera_subgrafo"}:
            return "#f8fafc", "#94a3b8"
        return "#ffffff", "#64748b"

    def etiqueta_estado_elemento(self, elemento: dict[str, Any]) -> str:
        if elemento.get("estado") == "expandible":
            target = elemento.get("target", {})
            return f"Abre {target.get('tipo', '')}"
        if elemento.get("estado") == "referencia_no_resuelta":
            return "Referencia no resuelta"
        if elemento.get("estado") == "ambigua":
            return "Referencia ambigua"
        if elemento.get("estado") == "referencia_dinamica":
            return "Referencia dinámica"
        if elemento.get("estado") == "deshabilitada":
            return "Entrada deshabilitada"
        if elemento.get("estado") == "fuera_subgrafo":
            return "Fuera del proceso"
        return elemento.get("tipo", "")


def main() -> None:
    configurar_rutas_tcl_tk()
    root = tk.Tk()
    estilo = ttk.Style()
    if "vista" in estilo.theme_names():
        estilo.theme_use("vista")
    ReconstructorPentahoApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
