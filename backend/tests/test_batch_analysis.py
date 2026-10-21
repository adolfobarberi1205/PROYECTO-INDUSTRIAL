from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from app.services import analysis_service
from app.services.process_documentation_service import (
    ProcessAlreadyDocumentedError,
    _ensure_unique_root_job,
)


class BatchAnalysisTests(TestCase):
    def test_reuses_inventory_and_keeps_independent_results(self) -> None:
        motor = Mock()
        inventory = {"jobs": {}, "transformaciones": {}}
        motor.cargar_xml_repositorio.return_value = (None, inventory)

        def analyze_process(**arguments):
            if arguments["nombre_job"] == "job_fallido":
                raise ValueError("Job de prueba no valido")

            self.assertIs(arguments["inventario_preparado"], inventory)
            self.assertIs(arguments["motor_preparado"], motor)
            return {"resumen": {"procesos": 1}}

        selections = [
            {
                "carpeta": "/PRUEBA",
                "nombre_job": "job_correcto",
                "nombre_proceso": "Proceso correcto",
                "descripcion": None,
            },
            {
                "carpeta": "/PRUEBA",
                "nombre_job": "job_fallido",
                "nombre_proceso": "Proceso fallido",
                "descripcion": None,
            },
        ]

        with TemporaryDirectory() as directory:
            xml_path = Path(directory) / "repository.xml"
            xml_path.write_text("<repository />", encoding="utf-8")

            with (
                patch.object(
                    analysis_service,
                    "cargar_motor_legacy",
                    return_value=motor,
                ),
                patch.object(
                    analysis_service,
                    "calcular_huella_repositorio",
                    return_value="a" * 64,
                ),
                patch.object(
                    analysis_service,
                    "analizar_proceso_etl",
                    side_effect=analyze_process,
                ) as analyze_mock,
            ):
                results = analysis_service.analizar_lote_procesos_etl(
                    ruta_xml=xml_path,
                    procesos=selections,
                    nombre_repositorio="Repositorio de prueba",
                    repositorio_id=3,
                )

        motor.cargar_xml_repositorio.assert_called_once()
        self.assertEqual(analyze_mock.call_count, 2)
        self.assertEqual(results[0]["estado"], "completado")
        self.assertEqual(results[1]["estado"], "fallido")
        self.assertIn("Job de prueba", results[1]["error"])

    def test_rejects_a_root_job_already_documented(self) -> None:
        session = Mock()
        session.execute.return_value.scalar_one_or_none.return_value = (
            "Proceso existente"
        )

        with self.assertRaisesRegex(
            ProcessAlreadyDocumentedError,
            "Proceso existente",
        ):
            _ensure_unique_root_job(
                session=session,
                repository_id=3,
                root_component_id=10,
                root_job_name="job_principal",
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
