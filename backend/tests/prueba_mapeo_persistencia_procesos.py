import json
from pathlib import Path

from app.services.process_persistence_mapper import (
    build_process_persistence_payload,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIRECTORY = PROJECT_ROOT / "resultados_temporales"


def get_latest_analysis() -> dict:
    result_files = sorted(
        RESULTS_DIRECTORY.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not result_files:
        raise FileNotFoundError("No hay resultados temporales para probar.")

    return json.loads(result_files[0].read_text(encoding="utf-8"))


analysis = get_latest_analysis()
payload = build_process_persistence_payload(
    analysis=analysis,
    repository_name="Repositorio Pentaho principal",
)

expected_processes = analysis["resumen"]["procesos"]
expected_relations = analysis["resumen"]["relaciones"]
expected_diagrams = analysis["resumen"]["diagramas"]

assert payload["counts"]["processes"] == expected_processes
assert payload["counts"]["relations"] == expected_relations
assert payload["counts"]["diagrams"] == expected_diagrams
assert sum(item["is_root"] for item in payload["scope"]) == 1

root_key = payload["documented_root"]["root_process_source_key"]
root_scope = next(
    item for item in payload["scope"] if item["process_source_key"] == root_key
)
assert root_scope["depth"] == 0

for diagram in payload["diagrams"]:
    for element in diagram["elements"]:
        assert element["source_x"] == element["display_x"]
        assert element["source_y"] == element["display_y"]

print("Mapeo permanente de procesos validado correctamente.")
print(payload["counts"])
print("La interfaz y el JSON temporal no fueron modificados.")
