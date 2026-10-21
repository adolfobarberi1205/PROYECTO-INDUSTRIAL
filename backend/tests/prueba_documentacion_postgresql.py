import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.services.process_documentation_service import (
    ProcessAlreadyDocumentedError,
    document_process,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIRECTORY = PROJECT_ROOT / "resultados_temporales"

result_files = sorted(
    RESULTS_DIRECTORY.glob("*.json"),
    key=lambda path: path.stat().st_mtime,
    reverse=True,
)

if not result_files:
    raise FileNotFoundError(
        "No hay un resultado temporal para probar la documentación."
    )

analysis = json.loads(result_files[0].read_text(encoding="utf-8"))
test_name = f"PRUEBA_DOCUMENTACION_{uuid4().hex}"

connection = get_engine().connect()
transaction = connection.begin()
session = Session(bind=connection, expire_on_commit=False)

try:
    saved = document_process(
        session=session,
        analysis=analysis,
        process_name=test_name,
    )

    if saved["nombre"] != test_name:
        raise AssertionError("No se conservó el nombre funcional.")

    if saved["guardado"]["componentes"] <= 0:
        raise AssertionError("No se guardaron componentes.")

    if saved["guardado"]["diagramas"] <= 0:
        raise AssertionError("No se guardaron diagramas.")

    try:
        document_process(
            session=session,
            analysis=analysis,
            process_name=test_name,
        )
    except ProcessAlreadyDocumentedError:
        pass
    else:
        raise AssertionError("No se detectó la documentación duplicada.")

    print("Persistencia PostgreSQL validada correctamente.")
    print(saved["guardado"])
    print("La prueba no dejó registros permanentes.")
finally:
    session.close()

    if transaction.is_active:
        transaction.rollback()

    connection.close()
