from __future__ import annotations

from unittest import TestCase
from unittest.mock import Mock

from app.services.process_documentation_service import (
    _save_transformation_details,
)
from app.services.process_persistence_mapper import (
    build_process_persistence_payload,
)


TECHNICAL_DETAIL = {
    "total_pasos": 2,
    "total_hops": 1,
    "tiene_sql": True,
    "scripts_sql": [
        {
            "paso": "Consultar origen",
            "tipo_paso": "TableInput",
            "sql": "SELECT * FROM origen.clientes WHERE host = '10.2.3.4'",
        }
    ],
    "tablas_consultadas": [
        {
            "esquema": "origen",
            "nombre": "clientes",
            "nombre_completo": "origen.clientes",
        }
    ],
    "tablas_destino": [
        {
            "esquema": "dwh",
            "nombre": "dim_cliente",
            "nombre_completo": "dwh.dim_cliente",
        }
    ],
    "usos_tablas": [
        {
            "paso": "Consultar origen",
            "tipo_uso": "CONSULTA",
            "esquema": "origen",
            "nombre": "clientes",
            "nombre_completo": "origen.clientes",
        },
        {
            "paso": "Guardar dimensión",
            "tipo_uso": "DESTINO",
            "esquema": "dwh",
            "nombre": "dim_cliente",
            "nombre_completo": "dwh.dim_cliente",
        },
    ],
    "variables": [],
    "parametros": [],
}


class TransformationDetailPersistenceTests(TestCase):
    def test_mapper_keeps_the_technical_detail(self) -> None:
        analysis = {
            "resultado": {
                "diagrama_principal_id": "job-root",
                "nodos": {
                    "job-root": {
                        "nombre": "job_root",
                        "tipo": "JOB",
                        "ruta": "/PRUEBA",
                    },
                    "trans-1": {
                        "nombre": "tr_cliente",
                        "tipo": "TRANSFORMATION",
                        "ruta": "/PRUEBA",
                    },
                },
                "relaciones": [],
                "diagramas": {
                    "job-root": {
                        "elementos": [],
                        "hops": [],
                    },
                    "trans-1": {
                        "elementos": [],
                        "hops": [],
                        "detalle_tecnico": TECHNICAL_DETAIL,
                    },
                },
            }
        }

        payload = build_process_persistence_payload(
            analysis=analysis,
            repository_name="Repositorio de prueba",
            process_name="Proceso de prueba",
        )
        transformation = next(
            diagram
            for diagram in payload["diagrams"]
            if diagram["process_source_key"] == "trans-1"
        )

        self.assertEqual(
            transformation["technical_detail"],
            TECHNICAL_DETAIL,
        )

    def test_persistence_sanitizes_sql_and_saves_table_roles(self) -> None:
        session = Mock()
        execution_result = Mock()
        execution_result.scalar_one.return_value = 41
        session.execute.return_value = execution_result

        counts = _save_transformation_details(
            session,
            process_version_id=7,
            diagrams=[
                {
                    "process_source_key": "trans-1",
                    "technical_detail": TECHNICAL_DETAIL,
                }
            ],
            component_ids={"trans-1": 12},
        )

        parameter_sets = [
            call.args[1]
            for call in session.execute.call_args_list
            if len(call.args) > 1 and isinstance(call.args[1], dict)
        ]
        sql_parameters = next(
            parameters
            for parameters in parameter_sets
            if "sql_sanitizado" in parameters
        )
        table_roles = {
            parameters["tipo_uso"]
            for parameters in parameter_sets
            if "tipo_uso" in parameters
        }

        self.assertIn("[IP_REDACTADA]", sql_parameters["sql_sanitizado"])
        self.assertNotIn("10.2.3.4", sql_parameters["sql_sanitizado"])
        self.assertEqual(table_roles, {"CONSULTA", "DESTINO"})
        self.assertEqual(counts["detalles_transformaciones"], 1)
        self.assertEqual(counts["scripts_sql_transformaciones"], 1)
        self.assertEqual(counts["tablas_transformaciones"], 2)


if __name__ == "__main__":
    import unittest

    unittest.main()
