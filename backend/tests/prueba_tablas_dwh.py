import xml.etree.ElementTree as ET

from app.services.warehouse_table_analysis_service import (
    analyze_warehouse_tables,
)


def create_step(
    name: str,
    step_type: str,
    **fields: str,
) -> ET.Element:
    step = ET.Element("step")
    ET.SubElement(step, "name").text = name
    ET.SubElement(step, "type").text = step_type

    for field_name, value in fields.items():
        ET.SubElement(step, field_name).text = value

    return step


transformation_xml = ET.Element("transformation")
transformation_xml.extend(
    [
        create_step(
            "Carga dimension",
            "DimensionLookup",
            schema="${DWH_SCHEMA}",
            table="dim_cliente",
            update="Y",
        ),
        create_step(
            "Solo consulta dimension",
            "DimensionLookup",
            schema="${DWH_SCHEMA}",
            table="dim_no_escrita",
            update="N",
        ),
        create_step(
            "Carga hechos",
            "TableOutput",
            schema="${DWH_SCHEMA}",
            table="fact_ventas",
        ),
        create_step(
            "Carga staging",
            "TableOutput",
            schema="STAGE",
            table="tmp_ventas",
        ),
        create_step(
            "SQL DWH",
            "ExecSQL",
            sql=(
                "CREATE TABLE ${DWH_SCHEMA}.dim_fecha (id number); "
                "INSERT INTO ${DWH_SCHEMA}.fact_pedidos SELECT 1; "
                "DELETE FROM ${DWH_SCHEMA}.fact_no_contar;"
            ),
        ),
    ]
)

inventory = {
    "transformaciones": {
        "TRANS|1": {
            "id": "TRANS|1",
            "nombre": "tr_prueba_dwh",
            "ruta": "/PRUEBAS",
            "xml_element": transformation_xml,
        }
    }
}
result = {
    "nodos": {
        "TRANS|1": {
            "id": "TRANS|1",
            "tipo": "TRANSFORMATION",
        }
    }
}
properties = {
    "DWH_SCHEMA": {
        "valor": "DWH_TEST",
    }
}

analysis = analyze_warehouse_tables(
    inventory=inventory,
    result=result,
    kettle_properties=properties,
)

assert analysis["total_dimensiones"] == 2
assert analysis["total_hechos"] == 2
assert analysis["total"] == 4

names = {table["nombre"] for table in analysis["tablas"]}
assert names == {
    "dim_cliente",
    "dim_fecha",
    "fact_ventas",
    "fact_pedidos",
}
assert "dim_no_escrita" not in names
assert "fact_no_contar" not in names
assert "tmp_ventas" not in names
assert all(
    table["esquema_resuelto"] == "DWH_TEST"
    for table in analysis["tablas"]
)

print("Deteccion de tablas DIM y FACT validada correctamente.")
print(
    f"Dimensiones: {analysis['total_dimensiones']} | "
    f"Hechos: {analysis['total_hechos']}"
)
