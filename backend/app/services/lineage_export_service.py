from __future__ import annotations

from typing import Any


def exportar_linajes_variables(
    *,
    motor: Any,
    inventario: dict[str, Any],
    resultado: dict[str, Any],
    propiedades_kettle: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calcula una vez los linajes usando el algoritmo legacy original."""

    propiedades = propiedades_kettle or {}
    informacion_variables = resultado.get("variables", {})

    if propiedades:
        motor.aplicar_clasificacion_kettle_variables(
            informacion_variables,
            propiedades,
        )

    nombres = motor.obtener_variables_disponibles(
        informacion_variables,
        propiedades,
    )

    linajes: dict[str, dict[str, Any]] = {}
    errores: list[dict[str, str]] = []

    for nombre in nombres:
        try:
            linajes[nombre] = motor.construir_linaje_variable(
                nombre,
                resultado,
                inventario,
                propiedades,
            )
        except Exception as error:
            errores.append(
                {
                    "variable": nombre,
                    "tipo": type(error).__name__,
                    "mensaje": str(error),
                }
            )

    return {
        "linajes": linajes,
        "errores": errores,
        "total": len(linajes),
    }
