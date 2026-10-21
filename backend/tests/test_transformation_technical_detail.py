from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.services.diagram_export_service import (
    _extraer_detalle_transformacion,
)


class TransformationTechnicalDetailTests(unittest.TestCase):
    def test_extracts_sql_tables_variables_and_parameters(self) -> None:
        transformation = ET.fromstring(
            """
            <transformation>
              <info>
                <parameters>
                  <parameter><name>FECHA_PROCESO</name></parameter>
                </parameters>
              </info>
              <step>
                <name>Consultar clientes</name>
                <type>TableInput</type>
                <sql>
                  SELECT c.id, e.nombre
                  FROM ${ODS_SCHEMA}.clientes c
                  JOIN catalogo.estados e ON e.id = c.estado_id
                  WHERE c.fecha = ${FECHA_PROCESO}
                </sql>
              </step>
              <step>
                <name>Buscar dimensión</name>
                <type>DatabaseLookup</type>
                <schema>${DWH_SCHEMA}</schema>
                <table>dim_cliente</table>
              </step>
              <step>
                <name>Guardar fact</name>
                <type>TableOutput</type>
                <schema>${DWH_SCHEMA}</schema>
                <table>fact_clientes</table>
              </step>
              <order>
                <hop><from>Consultar clientes</from><to>Buscar dimensión</to></hop>
                <hop><from>Buscar dimensión</from><to>Guardar fact</to></hop>
              </order>
            </transformation>
            """
        )

        detail = _extraer_detalle_transformacion(
            xml_element=transformation,
            elementos=[{}, {}, {}],
            hops=[{}, {}],
        )

        source_names = {
            table["nombre_completo"] for table in detail["tablas_consultadas"]
        }
        destination_names = {
            table["nombre_completo"] for table in detail["tablas_destino"]
        }
        usages = {
            (
                table["paso"],
                table["nombre_completo"],
                table["tipo_uso"],
            )
            for table in detail["usos_tablas"]
        }

        self.assertTrue(detail["tiene_sql"])
        self.assertEqual(detail["total_pasos"], 3)
        self.assertEqual(detail["total_hops"], 2)
        self.assertEqual(len(detail["scripts_sql"]), 1)
        self.assertIn("${ODS_SCHEMA}.clientes", source_names)
        self.assertIn("catalogo.estados", source_names)
        self.assertIn("${DWH_SCHEMA}.dim_cliente", source_names)
        self.assertIn("${DWH_SCHEMA}.fact_clientes", destination_names)
        self.assertIn(
            ("Consultar clientes", "${ODS_SCHEMA}.clientes", "CONSULTA"),
            usages,
        )
        self.assertIn(
            ("Buscar dimensión", "${DWH_SCHEMA}.dim_cliente", "LOOKUP"),
            usages,
        )
        self.assertIn(
            ("Guardar fact", "${DWH_SCHEMA}.fact_clientes", "DESTINO"),
            usages,
        )
        self.assertEqual(detail["parametros"], ["FECHA_PROCESO"])
        self.assertEqual(
            detail["variables"],
            ["DWH_SCHEMA", "FECHA_PROCESO", "ODS_SCHEMA"],
        )


if __name__ == "__main__":
    unittest.main()
