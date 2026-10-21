import re
from typing import Any


TERMINOS_SENSIBLES = {
    "password",
    "contraseña",
    "clave",
    "username",
    "usuario",
    "hostname",
    "host",
    "server",
    "servidor",
    "ip",
    "port",
    "puerto",
    "url",
    "token",
    "secret",
    "secreto",
    "credential",
    "credentials",
    "credencial",
    "credenciales",
}


def dividir_nombre_campo(nombre: str) -> set[str]:
    """
    Divide nombres como:
    - database_password
    - serverUrl
    - host-ip

    en palabras independientes.
    """

    nombre_separado = re.sub(
        r"([a-záéíóúñ])([A-ZÁÉÍÓÚÑ])",
        r"\1_\2",
        nombre,
    )

    palabras = re.split(
        r"[^a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]+",
        nombre_separado.casefold(),
    )

    return {
        palabra
        for palabra in palabras
        if palabra
    }


def buscar_campos_sensibles(
    contenido: Any,
    ruta_actual: str = "resultado",
) -> list[str]:
    """
    Devuelve las rutas de campos potencialmente sensibles.

    Solo compara palabras completas, evitando coincidencias
    accidentales como 'ip' dentro de 'tipo' o 'scripts'.
    """

    coincidencias: list[str] = []

    if isinstance(contenido, dict):
        for clave, valor in contenido.items():
            clave_texto = str(clave)
            nueva_ruta = f"{ruta_actual}.{clave_texto}"

            palabras_campo = dividir_nombre_campo(clave_texto)

            if palabras_campo.intersection(TERMINOS_SENSIBLES):
                coincidencias.append(nueva_ruta)

            coincidencias.extend(
                buscar_campos_sensibles(
                    contenido=valor,
                    ruta_actual=nueva_ruta,
                )
            )

    elif isinstance(contenido, list):
        for indice, elemento in enumerate(contenido):
            coincidencias.extend(
                buscar_campos_sensibles(
                    contenido=elemento,
                    ruta_actual=f"{ruta_actual}[{indice}]",
                )
            )

    return sorted(set(coincidencias))

PATRONES_CONTENIDO_SENSIBLE = {
    "direccion_ip": re.compile(
        r"(?<!\d)"
        r"(?:"
        r"(?:25[0-5]|2[0-4]\d|1?\d?\d)\."
        r"){3}"
        r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
        r"(?!\d)"
    ),
    "url": re.compile(
        r"(?i)\b(?:https?://|jdbc:[a-z0-9]+:)"
    ),
    "contrasena_cifrada": re.compile(
        r"(?i)\bencrypted\s+[a-f0-9]{8,}\b"
    ),
    "referencia_a_secreto": re.compile(
        r"(?i)\$\{[^}]*(?:"
        r"password|pass|pwd|clave|secret|token"
        r")[^}]*\}"
    ),
}


def buscar_contenido_sensible(
    contenido: Any,
    ruta_actual: str = "resultado",
) -> list[dict[str, str]]:
    """
    Busca patrones sensibles dentro de los valores de texto.

    Devuelve únicamente la ruta y la categoría encontrada.
    Nunca devuelve el valor original.
    """

    coincidencias: list[dict[str, str]] = []

    if isinstance(contenido, dict):
        for clave, valor in contenido.items():
            nueva_ruta = f"{ruta_actual}.{clave}"

            coincidencias.extend(
                buscar_contenido_sensible(
                    contenido=valor,
                    ruta_actual=nueva_ruta,
                )
            )

    elif isinstance(contenido, list):
        for indice, elemento in enumerate(contenido):
            coincidencias.extend(
                buscar_contenido_sensible(
                    contenido=elemento,
                    ruta_actual=f"{ruta_actual}[{indice}]",
                )
            )

    elif isinstance(contenido, str):
        for categoria, patron in PATRONES_CONTENIDO_SENSIBLE.items():
            if patron.search(contenido):
                coincidencias.append(
                    {
                        "ruta": ruta_actual,
                        "categoria": categoria,
                    }
                )

    coincidencias_unicas = {
        (item["ruta"], item["categoria"])
        for item in coincidencias
    }

    return [
        {
            "ruta": ruta,
            "categoria": categoria,
        }
        for ruta, categoria in sorted(coincidencias_unicas)
    ]