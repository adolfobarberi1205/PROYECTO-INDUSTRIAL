from __future__ import annotations

import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import UploadFile
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.legacy_adapter import cargar_motor_legacy


VARIABLE_PATTERN = re.compile(r"\$\{([^}]+)\}")
MAX_KETTLE_BYTES = 5 * 1024 * 1024
SENSITIVE_NAME_PATTERN = re.compile(
    r"(?:password|passwd|pwd|secret|token|credential|credencial|contrasena|contraseña)",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _property_index(
    properties: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Crea un indice interno sin exponer los valores en la respuesta."""

    return {
        _text(name).casefold(): _text(data.get("valor"))
        for name, data in properties.items()
        if isinstance(data, dict)
        and _text(name)
        and not SENSITIVE_NAME_PATTERN.search(_text(name))
    }


def resolve_stored_identifier(
    expression: str | None,
    properties: dict[str, str],
) -> dict[str, Any]:
    """Resuelve variables anidadas usadas en un esquema o tabla."""

    original = _text(expression)
    used: set[str] = set()
    missing: set[str] = set()

    if not original:
        return {
            "value": None,
            "used": used,
            "missing": missing,
            "has_variables": False,
        }

    has_variables = bool(VARIABLE_PATTERN.search(original))
    resolved = original

    for _ in range(20):
        matches = list(VARIABLE_PATTERN.finditer(resolved))
        if not matches:
            break

        changed = False

        def replace(match: re.Match[str]) -> str:
            nonlocal changed

            name = match.group(1).strip()
            key = name.casefold()

            if SENSITIVE_NAME_PATTERN.search(name):
                missing.add(name)
                return match.group(0)

            if key not in properties:
                missing.add(name)
                return match.group(0)

            used.add(name)
            changed = True
            return properties[key]

        next_value = VARIABLE_PATTERN.sub(replace, resolved)

        if not changed or next_value == resolved:
            resolved = next_value
            break

        resolved = next_value

    for match in VARIABLE_PATTERN.finditer(resolved):
        missing.add(match.group(1).strip())

    return {
        "value": resolved if not missing else None,
        "used": used,
        "missing": missing,
        "has_variables": has_variables,
    }


def update_repository_table_identifiers(
    session: Session,
    repository_id: int,
    properties: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Actualiza tablas DWH documentadas sin recrear sus procesos."""

    repository = session.execute(
        text(
            """
            SELECT id, nombre
            FROM repositorio
            WHERE id = :repositorio_id
            """
        ),
        {"repositorio_id": repository_id},
    ).mappings().one_or_none()

    if repository is None:
        raise FileNotFoundError(
            "No se encontro el repositorio documentado solicitado."
        )

    rows = session.execute(
        text(
            """
            SELECT
                dwh.id,
                dwh.proceso_id,
                dwh.categoria,
                dwh.nombre,
                dwh.nombre_resuelto,
                dwh.esquema,
                dwh.esquema_resuelto,
                dwh.nombre_completo
            FROM tabla_dwh AS dwh
            JOIN proceso_etl AS p ON p.id = dwh.proceso_id
            WHERE p.repositorio_id = :repositorio_id
            ORDER BY dwh.id
            """
        ),
        {"repositorio_id": repository_id},
    ).mappings().all()

    property_values = _property_index(properties)
    used_variables: set[str] = set()
    missing_variables: set[str] = set()
    updates: list[dict[str, Any]] = []
    affected_processes: set[int] = set()
    resolved_schemas = 0
    resolved_names = 0

    for row in rows:
        name_result = resolve_stored_identifier(
            row["nombre"],
            property_values,
        )
        schema_result = resolve_stored_identifier(
            row["esquema"],
            property_values,
        )

        used_variables.update(name_result["used"])
        used_variables.update(schema_result["used"])
        missing_variables.update(name_result["missing"])
        missing_variables.update(schema_result["missing"])

        resolved_name = name_result["value"]
        resolved_schema = schema_result["value"]

        if name_result["has_variables"] and resolved_name is not None:
            resolved_names += 1

        if schema_result["has_variables"] and resolved_schema is not None:
            resolved_schemas += 1

        display_name = resolved_name or _text(row["nombre"])
        display_schema = resolved_schema or _text(row["esquema"])
        full_name = (
            f"{display_schema}.{display_name}"
            if display_schema
            else display_name
        )

        update = {
            "table_id": int(row["id"]),
            "process_id": int(row["proceso_id"]),
            "category": row["categoria"],
            "resolved_name": resolved_name,
            "resolved_schema": resolved_schema,
            "full_name": full_name,
            "changed": (
                _text(row["nombre_resuelto"]) != _text(resolved_name)
                or _text(row["esquema_resuelto"]) != _text(resolved_schema)
                or _text(row["nombre_completo"]) != full_name
            ),
        }
        updates.append(update)
        affected_processes.add(update["process_id"])

    uniqueness_keys: set[tuple[int, str, str]] = set()

    for update in updates:
        key = (
            update["process_id"],
            _text(update["category"]),
            _text(update["full_name"]),
        )
        if key in uniqueness_keys:
            raise ValueError(
                "La configuracion Kettle produce nombres de tablas DWH duplicados."
            )
        uniqueness_keys.add(key)

    try:
        changed_updates = [update for update in updates if update["changed"]]

        for update in changed_updates:
            session.execute(
                text(
                    """
                    UPDATE tabla_dwh
                    SET nombre_resuelto = :resolved_name,
                        esquema_resuelto = :resolved_schema,
                        nombre_completo = :full_name
                    WHERE id = :table_id
                    """
                ),
                update,
            )

        session.execute(
            text(
                """
                UPDATE repositorio
                SET fecha_actualizacion = NOW()
                WHERE id = :repositorio_id
                """
            ),
            {"repositorio_id": repository_id},
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ValueError(
            "La configuracion Kettle genera nombres de tablas DWH duplicados."
        ) from error

    return {
        "repositorio_id": int(repository["id"]),
        "repositorio": repository["nombre"],
        "procesos_afectados": len(affected_processes),
        "tablas_revisadas": len(rows),
        "tablas_actualizadas": sum(update["changed"] for update in updates),
        "esquemas_resueltos": resolved_schemas,
        "nombres_resueltos": resolved_names,
        "variables_utilizadas": len(used_variables),
        "variables_no_resueltas": sorted(missing_variables),
        "archivo_almacenado": False,
    }


async def update_repository_from_kettle_upload(
    session: Session,
    repository_id: int,
    kettle: UploadFile,
) -> dict[str, Any]:
    """Procesa kettle.properties temporalmente y actualiza PostgreSQL."""

    original_name = kettle.filename or "kettle.properties"
    extension = Path(original_name).suffix.lower()

    if extension not in {".properties", ".txt"}:
        raise ValueError(
            "El archivo Kettle debe tener extension .properties o .txt."
        )

    with TemporaryDirectory(prefix="etl_kettle_") as directory:
        path = Path(directory) / "kettle.properties"
        total_bytes = 0

        await kettle.seek(0)

        with path.open("wb") as destination:
            while True:
                block = await kettle.read(1024 * 1024)
                if not block:
                    break

                total_bytes += len(block)
                if total_bytes > MAX_KETTLE_BYTES:
                    raise ValueError(
                        "El archivo Kettle supera el limite permitido de 5 MB."
                    )

                destination.write(block)

        if total_bytes == 0:
            raise ValueError("El archivo Kettle esta vacio.")

        motor = cargar_motor_legacy()
        properties = motor.cargar_kettle_properties(path)

        if not properties:
            raise ValueError(
                "No se encontraron propiedades validas en el archivo Kettle."
            )

        result = update_repository_table_identifiers(
            session=session,
            repository_id=repository_id,
            properties=properties,
        )
        result["propiedades_leidas"] = len(properties)
        return result
