import re
from typing import Any


PATRONES_REDACCION = {
    "direccion_ip": (
        re.compile(
            r"(?<!\d)"
            r"(?:"
            r"(?:25[0-5]|2[0-4]\d|1?\d?\d)\."
            r"){3}"
            r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
            r"(?!\d)"
        ),
        "[IP_REDACTADA]",
    ),
    "url": (
        re.compile(
            r"(?i)\b(?:https?://|jdbc:[a-z0-9]+:)[^\s\"'<>]+"
        ),
        "[URL_REDACTADA]",
    ),
    "credencial_cifrada": (
        re.compile(
            r"(?i)\bencrypted\s+[a-z0-9]+"
        ),
        "[CREDENCIAL_REDACTADA]",
    ),
    "referencia_a_secreto": (
        re.compile(
            r"(?i)\$\{[^}]*(?:"
            r"password|pass|pwd|clave|secret|token"
            r")[^}]*\}"
        ),
        "[REFERENCIA_SENSIBLE_REDACTADA]",
    ),
}


def sanitizar_metadatos(contenido: Any) -> dict[str, Any]:
    """
    Crea una copia sanitizada del resultado.

    No modifica el resultado original y nunca conserva valores
    detectados como IP, URL, credencial cifrada o referencia
    a una variable sensible.
    """

    contador = {
        categoria: 0
        for categoria in PATRONES_REDACCION
    }

    def sanitizar(elemento: Any) -> Any:
        if isinstance(elemento, dict):
            return {
                clave: sanitizar(valor)
                for clave, valor in elemento.items()
            }

        if isinstance(elemento, list):
            return [
                sanitizar(valor)
                for valor in elemento
            ]

        if isinstance(elemento, tuple):
            return tuple(
                sanitizar(valor)
                for valor in elemento
            )

        if isinstance(elemento, str):
            texto_limpio = elemento

            for categoria, (
                patron,
                reemplazo,
            ) in PATRONES_REDACCION.items():
                texto_limpio, cantidad = patron.subn(
                    reemplazo,
                    texto_limpio,
                )

                contador[categoria] += cantidad

            return texto_limpio

        return elemento

    contenido_limpio = sanitizar(contenido)

    return {
        "contenido": contenido_limpio,
        "redacciones": contador,
        "total_redacciones": sum(contador.values()),
    }