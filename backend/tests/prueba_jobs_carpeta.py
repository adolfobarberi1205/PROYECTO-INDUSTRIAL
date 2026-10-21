from pathlib import Path

from app.core.repository_parser import obtener_jobs_por_carpeta


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
RUTA_XML = RAIZ_PROYECTO / "pruebas_locales" / "20260611.xml"

CARPETA_PRUEBA = "/MODELOS/TRANSACCIONES"

resultado = obtener_jobs_por_carpeta(
    RUTA_XML,
    CARPETA_PRUEBA,
)

print(f"Carpeta analizada: {resultado['carpeta']}")
print(f"Jobs encontrados: {resultado['cantidad_jobs']}")

for job in resultado["jobs"]:
    print(f"- {job}")

if "job_Macro_Transacciones" not in resultado["jobs"]:
    raise AssertionError(
        "No se encontró el Job principal esperado."
    )

print("Prueba de selección de carpeta completada correctamente.")
