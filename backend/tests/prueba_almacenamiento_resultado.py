from pathlib import Path

from app.services.analysis_storage_service import (
    guardar_resultado_analisis,
    obtener_resultado_analisis,
)


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

DIRECTORIO_RESULTADOS = (
    RAIZ_PROYECTO
    / "resultados_temporales"
)

RESULTADO_PRUEBA = {
    "analisis": {
        "job_principal": "job_prueba",
        "estado": "completado",
    },
    "resumen": {
        "jobs": 2,
        "transformaciones": 5,
    },
    "seguridad": {
        "sanitizado": True,
    },
}


guardado = guardar_resultado_analisis(
    resultado=RESULTADO_PRUEBA,
    directorio_resultados=DIRECTORIO_RESULTADOS,
)

resultado_recuperado = obtener_resultado_analisis(
    id_analisis=guardado["id_analisis"],
    directorio_resultados=DIRECTORIO_RESULTADOS,
)

print("Resultado almacenado correctamente.")
print(f"ID del análisis: {guardado['id_analisis']}")
print(f"Tamaño: {guardado['tamano_bytes']} bytes")
print(
    "Job recuperado:",
    resultado_recuperado["analisis"]["job_principal"],
)

if resultado_recuperado != RESULTADO_PRUEBA:
    raise AssertionError(
        "El resultado recuperado no coincide con el original."
    )

print("El resultado recuperado coincide con el original.")