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

print("Auditoría del contenido completada.")
print(
    "Coincidencias potencialmente sensibles:",
    len(coincidencias),
)

if coincidencias:
    print("\nRUTAS Y CATEGORÍAS")

    for coincidencia in coincidencias:
        print(
            f"- {coincidencia['categoria']} | "
            f"{coincidencia['ruta']}"
        )
else:
    print(
        "No se detectaron direcciones IP, URLs, "
        "contraseñas cifradas ni referencias a secretos."
    )

print("\nNo se imprimieron los valores analizados.")