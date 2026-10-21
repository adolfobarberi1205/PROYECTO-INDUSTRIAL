from pathlib import Path

from app.core.repository_parser import validar_repositorio_xml


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
RUTA_XML = RAIZ_PROYECTO / "pruebas_locales" / "20260611.xml"

resultado = validar_repositorio_xml(RUTA_XML)

print("Repositorio XML reconocido correctamente.")
print(f"Archivo: {resultado['archivo']}")
print(f"Raíz XML: {resultado['raiz']}")
print(f"Tamaño: {resultado['tamano_bytes']} bytes")
