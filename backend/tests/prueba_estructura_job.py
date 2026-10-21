from pathlib import Path

from app.core.repository_parser import obtener_estructura_job


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
RUTA_XML = RAIZ_PROYECTO / "pruebas_locales" / "20260611.xml"

resultado = obtener_estructura_job(
    ruta_xml=RUTA_XML,
    carpeta="/MODELOS/TRANSACCIONES",
    nombre_job="job_Macro_Transacciones",
)

print("Estructura del Job obtenida correctamente.")
print(f"Job: {resultado['nombre']}")
print(f"Entradas: {resultado['cantidad_entradas']}")
print(f"Saltos: {resultado['cantidad_saltos']}")

print("\nELEMENTOS DEL JOB")

for entrada in resultado["entradas"]:
    print(
        f"- {entrada['nombre']} | "
        f"Tipo: {entrada['tipo']} | "
        f"Posición: ({entrada['x']}, {entrada['y']})"
    )

print("\nCONEXIONES DEL JOB")

for salto in resultado["saltos"]:
    print(
        f"- {salto['origen']} → {salto['destino']} | "
        f"Habilitado: {salto['habilitado']}"
    )

if resultado["cantidad_entradas"] != 13:
    raise AssertionError(
        "La cantidad de entradas no coincide con la prueba anterior."
    )

if resultado["cantidad_saltos"] != 17:
    raise AssertionError(
        "La cantidad de saltos no coincide con la prueba anterior."
    )

print("\nPrueba de estructura completada correctamente.")