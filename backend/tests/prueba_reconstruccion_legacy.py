from pathlib import Path
from time import perf_counter

from app.core.legacy_adapter import cargar_motor_legacy


RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
RUTA_XML = RAIZ_PROYECTO / "pruebas_locales" / "20260611.xml"

CARPETA = "/MODELOS/TRANSACCIONES"
NOMBRE_JOB = "job_Macro_Transacciones"


inicio = perf_counter()

print("Cargando motor actual...")
motor = cargar_motor_legacy()

print("Analizando repositorio completo...")
_, inventario = motor.cargar_xml_repositorio(RUTA_XML)

jobs_coincidentes = [
    job
    for job in inventario["jobs"].values()
    if job.get("nombre", "").strip().casefold()
    == NOMBRE_JOB.casefold()
    and motor.normalizar_ruta(job.get("ruta", "")).casefold()
    == motor.normalizar_ruta(CARPETA).casefold()
]

if not jobs_coincidentes:
    raise AssertionError(
        f"No se encontró el Job '{NOMBRE_JOB}' "
        f"en la carpeta '{CARPETA}'."
    )

if len(jobs_coincidentes) > 1:
    raise AssertionError(
        "Se encontró más de un Job con el mismo nombre y carpeta."
    )

job_principal = jobs_coincidentes[0]

print("Reconstruyendo proceso seleccionado...")
resultado = motor.reconstruir_proceso(
    job_principal_id=job_principal["id"],
    inventario=inventario,
)

resumen = resultado.get("proceso_reconstruido", {})
nodos = list(resultado.get("nodos", {}).values())

jobs_alcanzables = sum(
    1 for nodo in nodos
    if nodo.get("tipo") == "JOB"
)

transformaciones_alcanzables = sum(
    1 for nodo in nodos
    if nodo.get("tipo") == "TRANSFORMATION"
)

duracion = perf_counter() - inicio

print("\nRECONSTRUCCIÓN COMPLETADA")
print(f"Job principal: {job_principal['nombre']}")
print(f"Carpeta: {job_principal['ruta']}")

print("\nCATÁLOGO GLOBAL")
print(f"Jobs totales: {len(inventario['jobs'])}")
print(
    "Transformaciones totales: "
    f"{len(inventario['transformaciones'])}"
)
print(f"Carpetas totales: {len(inventario['carpetas'])}")

print("\nPROCESO SELECCIONADO")
print(f"Jobs alcanzables: {jobs_alcanzables}")
print(
    "Transformaciones alcanzables: "
    f"{transformaciones_alcanzables}"
)
print(f"Nodos: {len(nodos)}")
print(
    "Relaciones: "
    f"{len(resultado.get('relaciones', []))}"
)
print(
    "Parámetros: "
    f"{resumen.get('total_parametros', 0)}"
)
print(
    "Variables: "
    f"{resumen.get('total_variables', 0)}"
)
print(
    "Referencias no resueltas: "
    f"{len(resultado.get('referencias_no_resueltas', []))}"
)
print(
    "Referencias ambiguas: "
    f"{len(resultado.get('referencias_ambiguas', []))}"
)
print(
    "Referencias dinámicas: "
    f"{len(resultado.get('referencias_dinamicas', []))}"
)

print(f"\nDuración total: {duracion:.2f} segundos")
print("El análisis se ejecutó sin abrir Tkinter.")