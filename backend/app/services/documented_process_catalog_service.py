from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class DocumentedProcessNameConflictError(ValueError):
    """El repositorio ya contiene un proceso con el mismo nombre."""


def list_documented_processes(
    session: Session,
) -> list[dict[str, Any]]:
    """Devuelve el catálogo permanente de procesos ETL documentados."""

    rows = session.execute(
        text(
            """
            SELECT
                p.id,
                p.nombre,
                p.descripcion,
                r.id AS repositorio_id,
                r.nombre AS repositorio,
                r.fecha_carga,
                root.id AS job_principal_id,
                root.nombre AS job_principal,
                root.ruta AS carpeta,
                COUNT(DISTINCT pc.componente_id) AS componentes,
                COUNT(DISTINCT CASE
                    WHEN component.tipo = 'JOB' THEN component.id
                END) AS jobs,
                COUNT(DISTINCT CASE
                    WHEN component.tipo = 'TRANSFORMACION' THEN component.id
                END) AS transformaciones,
                COUNT(DISTINCT diagram.id) AS diagramas
                ,(SELECT COUNT(*) FROM tabla_dwh AS dwh
                  WHERE dwh.proceso_id = p.id
                    AND dwh.categoria = 'DIMENSION') AS dimensiones
                ,(SELECT COUNT(*) FROM tabla_dwh AS dwh
                  WHERE dwh.proceso_id = p.id
                    AND dwh.categoria = 'FACT') AS hechos
                ,(SELECT COUNT(*) FROM variable_etl AS variable
                  WHERE variable.proceso_id = p.id) AS variables
                ,(SELECT COUNT(*) FROM parametro_etl AS parameter
                  WHERE parameter.proceso_id = p.id) AS parametros
            FROM proceso_etl AS p
            JOIN repositorio AS r
              ON r.id = p.repositorio_id
            JOIN componente AS root
              ON root.id = p.job_principal_id
            LEFT JOIN proceso_componente AS pc
              ON pc.proceso_id = p.id
            LEFT JOIN componente AS component
              ON component.id = pc.componente_id
            LEFT JOIN diagrama AS diagram
              ON diagram.componente_id = component.id
            GROUP BY
                p.id,
                p.nombre,
                p.descripcion,
                r.id,
                r.nombre,
                r.fecha_carga,
                root.id,
                root.nombre,
                root.ruta
            ORDER BY p.id DESC
            """
        )
    ).mappings()

    return [
        {
            "id": int(row["id"]),
            "nombre": row["nombre"],
            "descripcion": row["descripcion"] or "",
            "repositorio": {
                "id": int(row["repositorio_id"]),
                "nombre": row["repositorio"],
                "fecha_carga": (
                    row["fecha_carga"].isoformat()
                    if row["fecha_carga"] is not None
                    else None
                ),
            },
            "job_principal": {
                "id": int(row["job_principal_id"]),
                "nombre": row["job_principal"],
                "carpeta": row["carpeta"] or "",
            },
            "resumen": {
                "componentes": int(row["componentes"] or 0),
                "jobs": int(row["jobs"] or 0),
                "transformaciones": int(row["transformaciones"] or 0),
                "diagramas": int(row["diagramas"] or 0),
                "dimensiones": int(row["dimensiones"] or 0),
                "hechos": int(row["hechos"] or 0),
                "variables": int(row["variables"] or 0),
                "parametros": int(row["parametros"] or 0),
            },
        }
        for row in rows
    ]


def update_documented_process(
    session: Session,
    process_id: int,
    name: str,
    description: str | None,
) -> dict[str, Any]:
    """Actualiza la informacion funcional de un proceso persistido."""

    normalized_name = name.strip()
    normalized_description = (description or "").strip() or None

    exists = session.execute(
        text("SELECT 1 FROM proceso_etl WHERE id = :process_id"),
        {"process_id": process_id},
    ).scalar_one_or_none()
    if exists is None:
        raise FileNotFoundError(
            f"No existe el proceso documentado con id '{process_id}'."
        )

    try:
        session.execute(
            text(
                """
                UPDATE proceso_etl
                SET nombre = :name,
                    descripcion = :description
                WHERE id = :process_id
                """
            ),
            {
                "process_id": process_id,
                "name": normalized_name,
                "description": normalized_description,
            },
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise DocumentedProcessNameConflictError(
            "Ya existe un proceso documentado con ese nombre en el repositorio."
        ) from error

    return next(
        process
        for process in list_documented_processes(session)
        if process["id"] == process_id
    )
