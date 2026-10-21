from pathlib import Path

from app.core.repository_parser import obtener_inventario_repositorio


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
RUTA_XML = RAIZ_PROYECTO / "pruebas_locales" / "20260611.xml"

resultado = obtener_inventario_repositorio(RUTA_XML)

print("Inventario del repositorio completado.")
print(f"Jobs encontrados: {resultado['jobs']}")
print(
    f"Transformaciones encontradas: "
    f"{resultado['transformaciones']}"
)
print(f"Carpetas encontradas: {resultado['cantidad_carpetas']}")