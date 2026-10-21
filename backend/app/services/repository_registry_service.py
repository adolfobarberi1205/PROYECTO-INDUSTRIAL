from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class RepositoryNameConflictError(ValueError):
    """El nombre funcional ya identifica otro repositorio."""


def _row_to_repository(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "nombre": row["nombre"],
        "fecha_carga": (
            row["fecha_carga"].isoformat()
            if row["fecha_carga"] is not None
            else None
        ),
        "version_actual": (
            int(row["numero_version"])
            if row["numero_version"] is not None
            else None
        ),
        "huella_actual": row["huella_sha256"],
    }


def list_repositories(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
                r.id,
                r.nombre,
                r.fecha_carga,
                vr.numero_version,
                COALESCE(vr.huella_sha256, r.huella_sha256) AS huella_sha256
            FROM repositorio AS r
            LEFT JOIN version_repositorio AS vr
              ON vr.repositorio_id = r.id
             AND vr.es_actual = TRUE
            ORDER BY LOWER(r.nombre), r.id
            """
        )
    ).mappings().all()

    return [_row_to_repository(row) for row in rows]


def find_repository_by_fingerprint(
    session: Session,
    fingerprint: str,
) -> dict[str, Any] | None:
    row = session.execute(
        text(
            """
            SELECT
                r.id,
                r.nombre,
                r.fecha_carga,
                vr.numero_version,
                vr.huella_sha256
            FROM version_repositorio AS vr
            JOIN repositorio AS r ON r.id = vr.repositorio_id
            WHERE vr.huella_sha256 = :huella_sha256
            LIMIT 1
            """
        ),
        {"huella_sha256": fingerprint},
    ).mappings().one_or_none()

    if row is None:
        row = session.execute(
            text(
                """
                SELECT
                    r.id,
                    r.nombre,
                    r.fecha_carga,
                    1 AS numero_version,
                    r.huella_sha256
                FROM repositorio AS r
                WHERE r.huella_sha256 = :huella_sha256
                LIMIT 1
                """
            ),
            {"huella_sha256": fingerprint},
        ).mappings().one_or_none()

    return _row_to_repository(row) if row is not None else None


def rename_repository(
    session: Session,
    repository_id: int,
    name: str,
) -> dict[str, Any]:
    normalized_name = name.strip()

    existing = session.execute(
        text(
            """
            SELECT id
            FROM repositorio
            WHERE LOWER(nombre) = LOWER(:nombre)
              AND id <> :repositorio_id
            LIMIT 1
            """
        ),
        {"nombre": normalized_name, "repositorio_id": repository_id},
    ).scalar_one_or_none()

    if existing is not None:
        raise RepositoryNameConflictError(
            f"Ya existe otro repositorio llamado '{normalized_name}'."
        )

    try:
        updated = session.execute(
            text(
                """
                UPDATE repositorio
                SET nombre = :nombre,
                    fecha_actualizacion = NOW()
                WHERE id = :repositorio_id
                RETURNING id
                """
            ),
            {"nombre": normalized_name, "repositorio_id": repository_id},
        ).scalar_one_or_none()

        if updated is None:
            session.rollback()
            raise FileNotFoundError(
                "No se encontro el repositorio solicitado."
            )

        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise RepositoryNameConflictError(
            f"Ya existe otro repositorio llamado '{normalized_name}'."
        ) from error

    repositories = list_repositories(session)
    return next(item for item in repositories if item["id"] == repository_id)


def ensure_repository_version(
    session: Session,
    *,
    name: str,
    fingerprint: str,
    repository_id: int | None = None,
) -> tuple[int, int, int, bool]:
    """Obtiene o crea el repositorio y la version del XML documentado."""

    matching = find_repository_by_fingerprint(session, fingerprint)
    if matching is not None:
        version_id = session.execute(
            text(
                """
                SELECT id
                FROM version_repositorio
                WHERE huella_sha256 = :huella_sha256
                LIMIT 1
                """
            ),
            {"huella_sha256": fingerprint},
        ).scalar_one()
        return (
            int(matching["id"]),
            int(version_id),
            int(matching["version_actual"] or 1),
            False,
        )

    if repository_id is None:
        same_name = session.execute(
            text(
                """
                SELECT id
                FROM repositorio
                WHERE LOWER(nombre) = LOWER(:nombre)
                LIMIT 1
                """
            ),
            {"nombre": name},
        ).scalar_one_or_none()

        if same_name is not None:
            raise RepositoryNameConflictError(
                "El nombre ya pertenece a otro repositorio. "
                "Seleccionelo como una actualizacion o use otro nombre."
            )

        repository_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO repositorio (
                        nombre,
                        huella_sha256,
                        fecha_actualizacion
                    )
                    VALUES (:nombre, :huella_sha256, NOW())
                    RETURNING id
                    """
                ),
                {"nombre": name, "huella_sha256": fingerprint},
            ).scalar_one()
        )
        version_number = 1
    else:
        repository_exists = session.execute(
            text("SELECT id FROM repositorio WHERE id = :id"),
            {"id": repository_id},
        ).scalar_one_or_none()
        if repository_exists is None:
            raise FileNotFoundError(
                "No se encontro el repositorio que se desea actualizar."
            )

        version_number = int(
            session.execute(
                text(
                    """
                    SELECT COALESCE(MAX(numero_version), 0) + 1
                    FROM version_repositorio
                    WHERE repositorio_id = :repositorio_id
                    """
                ),
                {"repositorio_id": repository_id},
            ).scalar_one()
        )
        session.execute(
            text(
                """
                UPDATE version_repositorio
                SET es_actual = FALSE
                WHERE repositorio_id = :repositorio_id
                  AND es_actual = TRUE
                """
            ),
            {"repositorio_id": repository_id},
        )
        session.execute(
            text(
                """
                UPDATE repositorio
                SET huella_sha256 = :huella_sha256,
                    fecha_actualizacion = NOW()
                WHERE id = :repositorio_id
                """
            ),
            {
                "huella_sha256": fingerprint,
                "repositorio_id": repository_id,
            },
        )

    version_id = int(
        session.execute(
            text(
                """
                INSERT INTO version_repositorio (
                    repositorio_id,
                    numero_version,
                    huella_sha256,
                    es_actual
                )
                VALUES (
                    :repositorio_id,
                    :numero_version,
                    :huella_sha256,
                    TRUE
                )
                RETURNING id
                """
            ),
            {
                "repositorio_id": repository_id,
                "numero_version": version_number,
                "huella_sha256": fingerprint,
            },
        ).scalar_one()
    )

    return repository_id, version_id, version_number, True
