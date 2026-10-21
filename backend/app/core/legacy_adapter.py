from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


def cargar_motor_legacy() -> ModuleType:
    """
    Carga el programa actual como módulo de Python sin ejecutar
    su interfaz Tkinter.
    """

    raiz_proyecto = Path(__file__).resolve().parents[3]
    ruta_legacy = raiz_proyecto / "legacy" / "reconstructor_pentaho.py"

    if not ruta_legacy.exists():
        raise FileNotFoundError(
            f"No se encontró el recolector actual: {ruta_legacy}"
        )

    spec = spec_from_file_location(
        "reconstructor_pentaho_legacy",
        ruta_legacy,
    )

    if spec is None or spec.loader is None:
        raise ImportError(
            "No fue posible preparar la carga del recolector actual."
        )

    modulo = module_from_spec(spec)
    spec.loader.exec_module(modulo)

    funciones_requeridas = [
        "cargar_xml_repositorio",
        "reconstruir_proceso",
    ]

    faltantes = [
        nombre
        for nombre in funciones_requeridas
        if not hasattr(modulo, nombre)
    ]

    if faltantes:
        raise AttributeError(
            "El recolector no contiene las funciones esperadas: "
            + ", ".join(faltantes)
        )

    return modulo