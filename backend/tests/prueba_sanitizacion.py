from pathlib import Path

from app.core.metadata_auditor import buscar_contenido_sensible
from app.core.metadata_sanitizer import sanitizar_metadatos
from app.services.analysis_service import analizar_proceso_etl


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
RUTA_XML = RAIZ_PROYECTO / "pruebas_locales" / "20260611.xml"

resultado_original = analizar_proceso_etl(
    ruta_xml=RUTA_XML,
    carpeta="/MODELOS/TRANSACCIONES",
    nombre_job="job_Macro_Transacciones",
)

auditoria_antes = buscar_contenido_sensible(
    resultado_original
)

sanitizacion = sanitizar_metadatos(
    resultado_original
)

resultado_limpio = sanitizacion["contenido"]

auditoria_despues = buscar_contenido_sensible(
    resultado_limpio
)

print("Sanitización completada.")

print(
    "Coincidencias antes:",
    len(auditoria_antes),
)

print(
    "Redacciones realizadas:",
    sanitizacion["total_redacciones"],
)

for categoria, cantidad in sanitizacion["redacciones"].items():
    print(f"- {categoria}: {cantidad}")

print(
    "Coincidencias después:",
    len(auditoria_despues),
)

if auditoria_despues:
    raise AssertionError(
        "El resultado sanitizado todavía contiene "
        "información potencialmente sensible."
    )

print(
    "\nEl resultado sanitizado quedó libre "
    "de los patrones auditados."
)
