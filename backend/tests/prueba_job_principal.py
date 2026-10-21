from pathlib import Path

from app.core.repository_parser import buscar_job_principal


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
RUTA_XML = RAIZ_PROYECTO / "pruebas_locales" / "20260611.xml"

resultado = buscar_job_principal(
    ruta_xml=RUTA_XML,
    carpeta="/MODELOS/TRANSACCIONES",
    nombre_job="job_Macro_Transacciones",
)

print("Job principal localizado correctamente.")
print(f"Nombre: {resultado['nombre']}")
print(f"Carpeta: {resultado['carpeta']}")
print(f"Entradas: {resultado['cantidad_entradas']}")
print(f"Saltos: {resultado['cantidad_saltos']}")