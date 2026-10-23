"""Synthetic end-to-end analyzer test; no corporate XML or database needed."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.services.analysis_service import analizar_proceso_etl
from app.services.analysis_storage_service import (
    guardar_resultado_analisis,
    obtener_resultado_analisis,
)


class MinimalAnalysisTests(TestCase):
    def test_analyzes_and_round_trips_a_synthetic_job_without_gui(self):
        xml = """<repository><jobs><job>
          <name>docker_smoke_job</name><directory>/SYNTHETIC</directory>
          <entries>
            <entry><name>START</name><type>SPECIAL</type><start>Y</start>
              <xloc>80</xloc><yloc>100</yloc></entry>
            <entry><name>Success</name><type>SUCCESS</type>
              <xloc>280</xloc><yloc>100</yloc></entry>
          </entries>
          <hops><hop><from>START</from><to>Success</to><enabled>Y</enabled>
            <unconditional>Y</unconditional></hop></hops>
        </job></jobs></repository>"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.xml"
            source.write_text(xml, encoding="utf-8")
            result = analizar_proceso_etl(
                ruta_xml=source,
                carpeta="/SYNTHETIC",
                nombre_job="docker_smoke_job",
            )
            self.assertEqual(result["resumen"]["jobs"], 1)
            self.assertTrue(result["seguridad"]["sanitizado"])
            diagrams = result["resultado"]["diagramas"]
            diagram = diagrams[result["resultado"]["diagrama_principal_id"]]
            self.assertEqual(len(diagram["elementos"]), 2)
            self.assertEqual(len(diagram["hops"]), 1)
            json.dumps(result, allow_nan=False)

            saved = guardar_resultado_analisis(
                resultado=result, directorio_resultados=root / "results",
            )
            source.unlink()
            loaded = obtener_resultado_analisis(
                id_analisis=saved["id_analisis"],
                directorio_resultados=root / "results",
            )
            self.assertEqual(loaded["resultado"]["diagramas"], diagrams)
