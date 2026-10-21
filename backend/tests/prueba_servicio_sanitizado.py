from pathlib import Path

from app.core.metadata_auditor import buscar_contenido_sensible
from app.services.analysis_service import analizar_proceso_etl


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
RUTA_XML = RAIZ_PROYECTO / "pruebas_locales" / "20260611.xml"

resultado = analizar_proceso_etl(
    ruta_xml=RUTA_XML,
    carpeta="/MODELOS/TRANSACCIONES",
    nombre_job="job_Macro_Transacciones",
)

coincidencias = buscar_contenido_sensible(resultado)

print("Servicio sanitizado ejecutado correctamente.")
print(
    "Sanitizado:",
    resultado["seguridad"]["sanitizado"],
)
print(
    "Redacciones:",
    resultado["seguridad"]["total_redacciones"],
)
print(
    "Coincidencias sensibles:",
    len(coincidencias),
)

print("\nRESUMEN CONSERVADO")
print(f"Jobs: {resultado['resumen']['jobs']}")
print(
    "Transformaciones:",
    resultado["resumen"]["transformaciones"],
)
print(f"Procesos: {resultado['resumen']['procesos']}")
print(f"Variables: {resultado['resumen']['variables']}")
print(f"Parámetros: {resultado['resumen']['parametros']}")

if coincidencias:
    raise AssertionError(
        "El servicio devolvió contenido sensible."
    )

if resultado["resumen"]["jobs"] != 6:
    raise AssertionError(
        "La sanitización alteró el resumen del proceso."
    )

if resultado["resumen"]["transformaciones"] != 42:
    raise AssertionError(
        "La sanitización alteró las transformaciones."
    )

print(
    "\nEl servicio entrega solamente "
    "el resultado sanitizado."
)