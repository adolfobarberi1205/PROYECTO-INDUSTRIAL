from pathlib import Path

from app.core.metadata_auditor import buscar_campos_sensibles
from app.services.analysis_service import analizar_proceso_etl


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
RUTA_XML = RAIZ_PROYECTO / "pruebas_locales" / "20260611.xml"

resultado = analizar_proceso_etl(
    ruta_xml=RUTA_XML,
    carpeta="/MODELOS/TRANSACCIONES",
    nombre_job="job_Macro_Transacciones",
)

coincidencias = buscar_campos_sensibles(resultado)

print("Auditoría de metadatos completada.")
print(
    "Campos potencialmente sensibles encontrados:",
    len(coincidencias),
)

if coincidencias:
    print("\nRUTAS ENCONTRADAS")

    for ruta in coincidencias:
        print(f"- {ruta}")
else:
    print(
        "No se encontraron campos potencialmente sensibles "
        "en el resultado."
    )

print("\nLa auditoría no mostró valores sensibles.")