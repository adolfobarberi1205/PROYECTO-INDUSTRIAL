from pathlib import Path

from app.services.analysis_service import analizar_proceso_etl


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
RUTA_XML = RAIZ_PROYECTO / "pruebas_locales" / "20260611.xml"

resultado = analizar_proceso_etl(
    ruta_xml=RUTA_XML,
    carpeta="/MODELOS/TRANSACCIONES",
    nombre_job="job_Macro_Transacciones",
)

analisis = resultado["analisis"]
resumen = resultado["resumen"]

print("Servicio de análisis ejecutado correctamente.")

print("\nANÁLISIS")
print(f"Job principal: {analisis['job_principal']}")
print(f"Carpeta: {analisis['carpeta']}")
print(f"Estado: {analisis['estado']}")
print(f"Duración: {analisis['duracion_segundos']} segundos")

print("\nRESUMEN")
print(f"Jobs: {resumen['jobs']}")
print(f"Transformaciones: {resumen['transformaciones']}")
print(f"Procesos: {resumen['procesos']}")
print(f"Relaciones: {resumen['relaciones']}")
print(f"Parámetros: {resumen['parametros']}")
print(f"Variables: {resumen['variables']}")
print(
    "Referencias no resueltas: "
    f"{resumen['referencias_no_resueltas']}"
)
print(
    "Referencias ambiguas: "
    f"{resumen['referencias_ambiguas']}"
)
print(
    "Referencias dinámicas: "
    f"{resumen['referencias_dinamicas']}"
)

VALORES_ESPERADOS = {
    "jobs": 6,
    "transformaciones": 42,
    "procesos": 48,
    "relaciones": 53,
    "parametros": 91,
    "variables": 162,
}

for campo, valor_esperado in VALORES_ESPERADOS.items():
    valor_obtenido = resumen[campo]

    if valor_obtenido != valor_esperado:
        raise AssertionError(
            f"El campo '{campo}' debería ser "
            f"{valor_esperado}, pero se obtuvo {valor_obtenido}."
        )

print("\nTodos los resultados coinciden con la prueba anterior.")