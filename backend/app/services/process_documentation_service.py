from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.process_persistence_mapper import (
    InvalidProcessAnalysisError,
    build_process_persistence_payload,
)
from app.services.analysis_metadata_persistence_service import (
    save_analysis_metadata,
)
from app.core.metadata_sanitizer import sanitizar_metadatos
from app.services.repository_registry_service import (
    RepositoryNameConflictError,
    ensure_repository_version,
)


class ProcessAlreadyDocumentedError(ValueError):
    """El nombre funcional ya fue registrado en el repositorio."""


PATRON_HUELLA_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _repository_name(analysis: dict[str, Any]) -> str:
    analysis_metadata = analysis.get("analisis", {})
    functional_name = str(
        analysis_metadata.get("nombre_repositorio", "")
    ).strip()

    if functional_name:
        return functional_name

    filename = str(analysis_metadata.get("archivo", "")).strip()

    if filename:
        return Path(filename).stem

    return "Repositorio Pentaho"


def _repository_fingerprint(analysis: dict[str, Any]) -> str | None:
    analysis_metadata = analysis.get("analisis", {})
    fingerprint = str(
        analysis_metadata.get("huella_sha256", "")
    ).strip().lower()

    if not fingerprint:
        return None

    if not PATRON_HUELLA_SHA256.fullmatch(fingerprint):
        raise InvalidProcessAnalysisError(
            "La huella SHA-256 del repositorio no es valida."
        )

    return fingerprint


def _repository_id_hint(analysis: dict[str, Any]) -> int | None:
    value = analysis.get("analisis", {}).get("repositorio_id")

    if value in (None, ""):
        return None

    try:
        repository_id = int(value)
    except (TypeError, ValueError) as error:
        raise InvalidProcessAnalysisError(
            "El identificador del repositorio no es valido."
        ) from error

    return repository_id if repository_id > 0 else None


def _process_structure_fingerprint(payload: dict[str, Any]) -> str:
    structural_payload = {
        "root": payload["documented_root"]["root_process_source_key"],
        "processes": payload["processes"],
        "relations": payload["relations"],
        "diagrams": payload["diagrams"],
    }
    serialized = json.dumps(
        structural_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _get_or_create_repository(
    session: Session,
    name: str,
    fingerprint: str | None,
) -> int:
    if fingerprint:
        repository_id = session.execute(
            text(
                """
                SELECT id
                FROM repositorio
                WHERE huella_sha256 = :huella_sha256
                LIMIT 1
                """
            ),
            {"huella_sha256": fingerprint},
        ).scalar_one_or_none()

        if repository_id is not None:
            return int(repository_id)

    existing_id = session.execute(
        text(
            """
            SELECT id
            FROM repositorio
            WHERE LOWER(nombre) = LOWER(:nombre)
            ORDER BY id
            LIMIT 1
            """
        ),
        {"nombre": name},
    ).scalar_one_or_none()

    if existing_id is not None:
        if fingerprint:
            session.execute(
                text(
                    """
                    UPDATE repositorio
                    SET huella_sha256 = COALESCE(
                        huella_sha256,
                        :huella_sha256
                    )
                    WHERE id = :repositorio_id
                    """
                ),
                {
                    "huella_sha256": fingerprint,
                    "repositorio_id": existing_id,
                },
            )
        return int(existing_id)

    return int(
        session.execute(
            text(
                """
                INSERT INTO repositorio (
                    nombre,
                    huella_sha256
                )
                VALUES (
                    :nombre,
                    :huella_sha256
                )
                RETURNING id
                """
            ),
            {
                "nombre": name,
                "huella_sha256": fingerprint,
            },
        ).scalar_one()
    )


def _ensure_unique_process_name(
    session: Session,
    repository_id: int,
    process_name: str,
) -> None:
    existing_id = session.execute(
        text(
            """
            SELECT id
            FROM proceso_etl
            WHERE repositorio_id = :repositorio_id
              AND LOWER(nombre) = LOWER(:nombre)
            LIMIT 1
            """
        ),
        {
            "repositorio_id": repository_id,
            "nombre": process_name,
        },
    ).scalar_one_or_none()

    if existing_id is not None:
        raise ProcessAlreadyDocumentedError(
            f"Ya existe un proceso documentado llamado '{process_name}'."
        )


def _ensure_unique_root_job(
    session: Session,
    repository_id: int,
    root_component_id: int,
    root_job_name: str,
) -> None:
    existing_process = session.execute(
        text(
            """
            SELECT nombre
            FROM proceso_etl
            WHERE repositorio_id = :repositorio_id
              AND job_principal_id = :job_principal_id
            LIMIT 1
            """
        ),
        {
            "repositorio_id": repository_id,
            "job_principal_id": root_component_id,
        },
    ).scalar_one_or_none()

    if existing_process is not None:
        raise ProcessAlreadyDocumentedError(
            f"El Job principal '{root_job_name}' ya pertenece al "
            f"proceso documentado '{existing_process}'."
        )


def _save_components(
    session: Session,
    repository_id: int,
    processes: list[dict[str, Any]],
) -> dict[str, int]:
    component_ids: dict[str, int] = {}

    statement = text(
        """
        INSERT INTO componente (
            repositorio_id,
            clave_origen,
            nombre,
            tipo,
            ruta
        )
        VALUES (
            :repositorio_id,
            :clave_origen,
            :nombre,
            :tipo,
            :ruta
        )
        ON CONFLICT (repositorio_id, clave_origen)
        DO UPDATE SET
            nombre = EXCLUDED.nombre,
            tipo = EXCLUDED.tipo,
            ruta = EXCLUDED.ruta
        RETURNING id
        """
    )

    for process in processes:
        source_key = str(process["source_key"])
        process_type = (
            "TRANSFORMACION"
            if process["process_type"] == "TRANSFORMATION"
            else "JOB"
        )
        component_id = session.execute(
            statement,
            {
                "repositorio_id": repository_id,
                "clave_origen": source_key,
                "nombre": process["name"],
                "tipo": process_type,
                "ruta": process["path"] or None,
            },
        ).scalar_one()
        component_ids[source_key] = int(component_id)

    return component_ids


def _save_process_scope(
    session: Session,
    repository_id: int,
    repository_version_id: int,
    process_name: str,
    description: str | None,
    structure_fingerprint: str,
    root_component_id: int,
    component_ids: dict[str, int],
) -> int:
    process_id = int(
        session.execute(
            text(
                """
                INSERT INTO proceso_etl (
                    repositorio_id,
                    version_repositorio_id,
                    nombre,
                    descripcion,
                    job_principal_id,
                    huella_estructura
                )
                VALUES (
                    :repositorio_id,
                    :version_repositorio_id,
                    :nombre,
                    :descripcion,
                    :job_principal_id,
                    :huella_estructura
                )
                RETURNING id
                """
            ),
            {
                "repositorio_id": repository_id,
                "version_repositorio_id": repository_version_id,
                "nombre": process_name,
                "descripcion": description,
                "job_principal_id": root_component_id,
                "huella_estructura": structure_fingerprint,
            },
        ).scalar_one()
    )

    membership_statement = text(
        """
        INSERT INTO proceso_componente (proceso_id, componente_id)
        VALUES (:proceso_id, :componente_id)
        ON CONFLICT (proceso_id, componente_id) DO NOTHING
        """
    )

    for component_id in component_ids.values():
        session.execute(
            membership_statement,
            {
                "proceso_id": process_id,
                "componente_id": component_id,
            },
        )

    return process_id


def _save_process_version(
    session: Session,
    *,
    process_id: int,
    repository_version_id: int,
    structure_fingerprint: str,
    analysis: dict[str, Any],
) -> int:
    snapshot = json.dumps(
        analysis,
        ensure_ascii=False,
        default=str,
    )

    return int(
        session.execute(
            text(
                """
                INSERT INTO version_proceso_etl (
                    proceso_id,
                    version_repositorio_id,
                    numero_version,
                    estado,
                    es_actual,
                    huella_estructura,
                    snapshot_documentacion
                )
                VALUES (
                    :proceso_id,
                    :version_repositorio_id,
                    1,
                    'VIGENTE',
                    TRUE,
                    :huella_estructura,
                    CAST(:snapshot AS JSONB)
                )
                RETURNING id
                """
            ),
            {
                "proceso_id": process_id,
                "version_repositorio_id": repository_version_id,
                "huella_estructura": structure_fingerprint,
                "snapshot": snapshot,
            },
        ).scalar_one()
    )


def _save_relations(
    session: Session,
    relations: list[dict[str, Any]],
    component_ids: dict[str, int],
) -> int:
    statement = text(
        """
        INSERT INTO relacion_componente (
            componente_origen_id,
            componente_destino_id,
            tipo_relacion
        )
        VALUES (
            :origen_id,
            :destino_id,
            :tipo_relacion
        )
        ON CONFLICT (
            componente_origen_id,
            componente_destino_id,
            tipo_relacion
        ) DO NOTHING
        """
    )
    saved = 0

    for relation in relations:
        origin_id = component_ids.get(relation["source_process_key"])
        target_key = relation.get("target_process_key")
        target_id = component_ids.get(target_key) if target_key else None

        if origin_id is None or target_id is None:
            continue

        result = session.execute(
            statement,
            {
                "origen_id": origin_id,
                "destino_id": target_id,
                "tipo_relacion": "EJECUTA",
            },
        )
        saved += max(result.rowcount or 0, 0)

    return saved


def _save_diagrams(
    session: Session,
    diagrams: list[dict[str, Any]],
    component_ids: dict[str, int],
) -> dict[str, int]:
    diagram_statement = text(
        """
        INSERT INTO diagrama (
            componente_id,
            ancho_recomendado,
            alto_recomendado
        )
        VALUES (
            :componente_id,
            :ancho,
            :alto
        )
        ON CONFLICT (componente_id)
        DO UPDATE SET
            ancho_recomendado = EXCLUDED.ancho_recomendado,
            alto_recomendado = EXCLUDED.alto_recomendado
        RETURNING id
        """
    )
    element_statement = text(
        """
        INSERT INTO elemento_diagrama (
            diagrama_id,
            clave_origen,
            nombre,
            tipo,
            categoria,
            x_original,
            y_original,
            x_visual,
            y_visual,
            habilitado,
            navegable,
            componente_destino_id,
            estado_destino,
            orden
        )
        VALUES (
            :diagrama_id,
            :clave_origen,
            :nombre,
            :tipo,
            :categoria,
            :x_original,
            :y_original,
            :x_visual,
            :y_visual,
            :habilitado,
            :navegable,
            :componente_destino_id,
            :estado_destino,
            :orden
        )
        ON CONFLICT (diagrama_id, clave_origen)
        DO UPDATE SET
            nombre = EXCLUDED.nombre,
            tipo = EXCLUDED.tipo,
            categoria = EXCLUDED.categoria,
            x_original = EXCLUDED.x_original,
            y_original = EXCLUDED.y_original,
            habilitado = EXCLUDED.habilitado,
            navegable = EXCLUDED.navegable,
            componente_destino_id = EXCLUDED.componente_destino_id,
            estado_destino = EXCLUDED.estado_destino,
            orden = EXCLUDED.orden
        """
    )
    hop_statement = text(
        """
        INSERT INTO hop_diagrama (
            diagrama_id,
            clave_origen,
            nombre_origen,
            nombre_destino,
            habilitado,
            evaluacion,
            incondicional,
            orden
        )
        VALUES (
            :diagrama_id,
            :clave_origen,
            :nombre_origen,
            :nombre_destino,
            :habilitado,
            :evaluacion,
            :incondicional,
            :orden
        )
        ON CONFLICT (diagrama_id, clave_origen)
        DO UPDATE SET
            nombre_origen = EXCLUDED.nombre_origen,
            nombre_destino = EXCLUDED.nombre_destino,
            habilitado = EXCLUDED.habilitado,
            evaluacion = EXCLUDED.evaluacion,
            incondicional = EXCLUDED.incondicional,
            orden = EXCLUDED.orden
        """
    )
    counts = {"diagramas": 0, "elementos": 0, "hops": 0}

    for diagram in diagrams:
        component_id = component_ids.get(diagram["process_source_key"])

        if component_id is None:
            continue

        diagram_id = int(
            session.execute(
                diagram_statement,
                {
                    "componente_id": component_id,
                    "ancho": diagram["recommended_width"],
                    "alto": diagram["recommended_height"],
                },
            ).scalar_one()
        )
        counts["diagramas"] += 1

        for element in diagram["elements"]:
            target_key = element.get("target_process_key")
            session.execute(
                element_statement,
                {
                    "diagrama_id": diagram_id,
                    "clave_origen": element["source_key"],
                    "nombre": element["name"],
                    "tipo": element["element_type"],
                    "categoria": element["category"],
                    "x_original": element["source_x"],
                    "y_original": element["source_y"],
                    "x_visual": element["display_x"],
                    "y_visual": element["display_y"],
                    "habilitado": element["enabled"],
                    "navegable": element["navigable"],
                    "componente_destino_id": (
                        component_ids.get(target_key) if target_key else None
                    ),
                    "estado_destino": element.get("target_status"),
                    "orden": element["display_order"],
                },
            )
            counts["elementos"] += 1

        for hop in diagram["hops"]:
            session.execute(
                hop_statement,
                {
                    "diagrama_id": diagram_id,
                    "clave_origen": hop["source_key"],
                    "nombre_origen": hop["origin_name"],
                    "nombre_destino": hop["destination_name"],
                    "habilitado": hop["enabled"],
                    "evaluacion": hop.get("evaluation"),
                    "incondicional": hop["unconditional"],
                    "orden": hop["display_order"],
                },
            )
            counts["hops"] += 1

    return counts


def _save_transformation_details(
    session: Session,
    *,
    process_version_id: int,
    diagrams: list[dict[str, Any]],
    component_ids: dict[str, int],
) -> dict[str, int]:
    """Guarda el detalle tecnico normalizado de las transformaciones."""

    detail_statement = text(
        """
        INSERT INTO detalle_transformacion (
            version_proceso_id,
            componente_id,
            total_pasos,
            total_hops,
            tiene_sql
        )
        VALUES (
            :version_proceso_id,
            :componente_id,
            :total_pasos,
            :total_hops,
            :tiene_sql
        )
        ON CONFLICT (version_proceso_id, componente_id)
        DO UPDATE SET
            total_pasos = EXCLUDED.total_pasos,
            total_hops = EXCLUDED.total_hops,
            tiene_sql = EXCLUDED.tiene_sql,
            fecha_extraccion = NOW()
        RETURNING id
        """
    )
    script_statement = text(
        """
        INSERT INTO script_sql_transformacion (
            detalle_transformacion_id,
            nombre_paso,
            tipo_paso,
            sql_sanitizado,
            hash_sql,
            orden
        )
        VALUES (
            :detalle_id,
            :nombre_paso,
            :tipo_paso,
            :sql_sanitizado,
            :hash_sql,
            :orden
        )
        """
    )
    table_statement = text(
        """
        INSERT INTO tabla_transformacion (
            detalle_transformacion_id,
            nombre_paso,
            esquema,
            nombre,
            nombre_completo,
            tipo_uso,
            orden
        )
        VALUES (
            :detalle_id,
            :nombre_paso,
            :esquema,
            :nombre,
            :nombre_completo,
            :tipo_uso,
            :orden
        )
        ON CONFLICT (
            detalle_transformacion_id,
            nombre_paso,
            nombre_completo,
            tipo_uso
        ) DO NOTHING
        """
    )
    counts = {
        "detalles_transformaciones": 0,
        "scripts_sql_transformaciones": 0,
        "tablas_transformaciones": 0,
    }

    for diagram in diagrams:
        detail = diagram.get("technical_detail")

        if not isinstance(detail, dict) or not detail:
            continue

        component_id = component_ids.get(diagram["process_source_key"])

        if component_id is None:
            continue

        scripts = [
            item
            for item in detail.get("scripts_sql", [])
            if isinstance(item, dict)
        ]
        detail_id = int(
            session.execute(
                detail_statement,
                {
                    "version_proceso_id": process_version_id,
                    "componente_id": component_id,
                    "total_pasos": max(int(detail.get("total_pasos") or 0), 0),
                    "total_hops": max(int(detail.get("total_hops") or 0), 0),
                    "tiene_sql": bool(scripts),
                },
            ).scalar_one()
        )
        counts["detalles_transformaciones"] += 1

        session.execute(
            text(
                """
                DELETE FROM script_sql_transformacion
                WHERE detalle_transformacion_id = :detalle_id
                """
            ),
            {"detalle_id": detail_id},
        )
        session.execute(
            text(
                """
                DELETE FROM tabla_transformacion
                WHERE detalle_transformacion_id = :detalle_id
                """
            ),
            {"detalle_id": detail_id},
        )

        for order, script in enumerate(scripts):
            sql = str(script.get("sql") or "").strip()

            if not sql:
                continue

            sanitized_sql = str(
                sanitizar_metadatos(sql)["contenido"]
            )
            session.execute(
                script_statement,
                {
                    "detalle_id": detail_id,
                    "nombre_paso": (
                        str(script.get("paso") or f"Paso SQL {order + 1}")
                    ),
                    "tipo_paso": str(script.get("tipo_paso") or "") or None,
                    "sql_sanitizado": sanitized_sql,
                    "hash_sql": sha256(
                        sanitized_sql.encode("utf-8")
                    ).hexdigest(),
                    "orden": order,
                },
            )
            counts["scripts_sql_transformaciones"] += 1

        table_usages = [
            item
            for item in detail.get("usos_tablas", [])
            if isinstance(item, dict)
        ]

        if not table_usages:
            table_usages = [
                {**item, "tipo_uso": "CONSULTA", "paso": None}
                for item in detail.get("tablas_consultadas", [])
                if isinstance(item, dict)
            ] + [
                {**item, "tipo_uso": "DESTINO", "paso": None}
                for item in detail.get("tablas_destino", [])
                if isinstance(item, dict)
            ]

        seen_tables: set[tuple[str, str, str]] = set()
        table_order = 0

        for table_usage in table_usages:
            table_name = str(table_usage.get("nombre") or "").strip()
            full_name = str(
                table_usage.get("nombre_completo") or table_name
            ).strip()
            usage_type = str(
                table_usage.get("tipo_uso") or "CONSULTA"
            ).strip().upper()
            step_name = str(table_usage.get("paso") or "").strip()

            if usage_type not in {"CONSULTA", "DESTINO", "LOOKUP"}:
                usage_type = "CONSULTA"

            if not table_name or not full_name:
                continue

            table_key = (
                step_name.casefold(),
                full_name.casefold(),
                usage_type,
            )

            if table_key in seen_tables:
                continue

            seen_tables.add(table_key)
            session.execute(
                table_statement,
                {
                    "detalle_id": detail_id,
                    "nombre_paso": step_name or None,
                    "esquema": (
                        str(table_usage.get("esquema") or "").strip() or None
                    ),
                    "nombre": table_name,
                    "nombre_completo": full_name,
                    "tipo_uso": usage_type,
                    "orden": table_order,
                },
            )
            table_order += 1
            counts["tablas_transformaciones"] += 1

    return counts


def document_process(
    *,
    session: Session,
    analysis: dict[str, Any],
    process_name: str,
    description: str | None = None,
) -> dict[str, Any]:
    repository_name = _repository_name(analysis)
    repository_fingerprint = _repository_fingerprint(analysis)
    repository_id_hint = _repository_id_hint(analysis)

    if repository_fingerprint is None:
        raise InvalidProcessAnalysisError(
            "El analisis no contiene la huella del repositorio."
        )

    payload = build_process_persistence_payload(
        analysis,
        repository_name=repository_name,
        process_name=process_name,
    )
    structure_fingerprint = _process_structure_fingerprint(payload)

    try:
        (
            repository_id,
            repository_version_id,
            repository_version_number,
            repository_version_created,
        ) = ensure_repository_version(
            session=session,
            name=payload["repository"]["name"],
            fingerprint=repository_fingerprint,
            repository_id=repository_id_hint,
        )
        _ensure_unique_process_name(
            session,
            repository_id,
            payload["documented_root"]["name"],
        )
        component_ids = _save_components(
            session,
            repository_id,
            payload["processes"],
        )
        root_key = payload["documented_root"][
            "root_process_source_key"
        ]
        root_component_id = component_ids.get(root_key)

        if root_component_id is None:
            raise InvalidProcessAnalysisError(
                "No fue posible relacionar el Job principal."
            )

        _ensure_unique_root_job(
            session,
            repository_id,
            root_component_id,
            next(
                (
                    process["name"]
                    for process in payload["processes"]
                    if process["source_key"] == root_key
                ),
                root_key,
            ),
        )

        process_id = _save_process_scope(
            session,
            repository_id,
            repository_version_id,
            payload["documented_root"]["name"],
            description.strip() if description else None,
            structure_fingerprint,
            root_component_id,
            component_ids,
        )
        relation_count = _save_relations(
            session,
            payload["relations"],
            component_ids,
        )
        diagram_counts = _save_diagrams(
            session,
            payload["diagrams"],
            component_ids,
        )
        metadata_counts = save_analysis_metadata(
            session=session,
            process_id=process_id,
            analysis=analysis,
        )
        process_version_id = _save_process_version(
            session,
            process_id=process_id,
            repository_version_id=repository_version_id,
            structure_fingerprint=structure_fingerprint,
            analysis=analysis,
        )
        transformation_detail_counts = _save_transformation_details(
            session,
            process_version_id=process_version_id,
            diagrams=payload["diagrams"],
            component_ids=component_ids,
        )
        session.commit()

    except (
        ProcessAlreadyDocumentedError,
        InvalidProcessAnalysisError,
        RepositoryNameConflictError,
    ):
        session.rollback()
        raise
    except IntegrityError as error:
        session.rollback()
        raise ProcessAlreadyDocumentedError(
            "El proceso ya fue documentado o sus datos están duplicados."
        ) from error
    except Exception:
        session.rollback()
        raise

    return {
        "proceso_id": process_id,
        "repositorio_id": repository_id,
        "repositorio": payload["repository"]["name"],
        "version_repositorio_id": repository_version_id,
        "numero_version_repositorio": repository_version_number,
        "version_repositorio_creada": repository_version_created,
        "version_proceso_id": process_version_id,
        "nombre": payload["documented_root"]["name"],
        "job_principal": root_key,
        "guardado": {
            "componentes": len(component_ids),
            "relaciones": relation_count,
            **diagram_counts,
            **metadata_counts,
            **transformation_detail_counts,
        },
    }
