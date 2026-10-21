from unittest import TestCase

from app.services.repository_kettle_update_service import (
    _property_index,
    resolve_stored_identifier,
    update_repository_table_identifiers,
)


class _FakeResult:
    def __init__(self, *, one=None, rows=None) -> None:
        self.one = one
        self.rows = rows or []

    def mappings(self):
        return self

    def one_or_none(self):
        return self.one

    def all(self):
        return self.rows


class _FakeSession:
    def __init__(self) -> None:
        self.table_updates = []
        self.committed = False

    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}

        if "FROM repositorio" in sql:
            return _FakeResult(one={"id": 4, "nombre": "Repositorio"})

        if "FROM tabla_dwh" in sql:
            return _FakeResult(
                rows=[
                    {
                        "id": 9,
                        "proceso_id": 2,
                        "categoria": "DIMENSION",
                        "nombre": "dim_cliente",
                        "nombre_resuelto": "dim_cliente",
                        "esquema": "${esquema_dwh_dest}",
                        "esquema_resuelto": "${esquema_dwh_dest}",
                        "nombre_completo": (
                            "${esquema_dwh_dest}.dim_cliente"
                        ),
                    }
                ]
            )

        if "UPDATE tabla_dwh" in sql:
            self.table_updates.append(parameters)

        return _FakeResult()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass


class RepositoryKettleUpdateTests(TestCase):
    def test_resolves_a_stored_schema(self) -> None:
        properties = _property_index(
            {
                "esquema_dwh_dest": {
                    "valor": "DWH_PRUEBA",
                }
            }
        )

        result = resolve_stored_identifier(
            "${esquema_dwh_dest}",
            properties,
        )

        self.assertEqual(result["value"], "DWH_PRUEBA")
        self.assertEqual(result["used"], {"esquema_dwh_dest"})
        self.assertEqual(result["missing"], set())

    def test_resolves_nested_properties(self) -> None:
        properties = _property_index(
            {
                "catalog": {"valor": "DWH"},
                "schema": {"valor": "${catalog}_PROD"},
            }
        )

        result = resolve_stored_identifier(
            "${schema}.dim_cliente",
            properties,
        )

        self.assertEqual(result["value"], "DWH_PROD.dim_cliente")

    def test_reports_a_missing_property_without_a_value(self) -> None:
        result = resolve_stored_identifier(
            "${esquema_desconocido}",
            {},
        )

        self.assertIsNone(result["value"])
        self.assertEqual(result["missing"], {"esquema_desconocido"})

    def test_does_not_make_sensitive_values_persistable(self) -> None:
        properties = _property_index(
            {"db_password": {"valor": "super-secret"}}
        )

        result = resolve_stored_identifier(
            "${db_password}",
            properties,
        )

        self.assertIsNone(result["value"])
        self.assertEqual(result["missing"], {"db_password"})

    def test_updates_existing_documentation_without_recreating_processes(
        self,
    ) -> None:
        session = _FakeSession()

        result = update_repository_table_identifiers(
            session=session,
            repository_id=4,
            properties={
                "esquema_dwh_dest": {"valor": "DWH_PRUEBA"},
            },
        )

        self.assertTrue(session.committed)
        self.assertEqual(len(session.table_updates), 1)
        self.assertEqual(
            session.table_updates[0]["resolved_schema"],
            "DWH_PRUEBA",
        )
        self.assertEqual(
            session.table_updates[0]["full_name"],
            "DWH_PRUEBA.dim_cliente",
        )
        self.assertEqual(result["procesos_afectados"], 1)
        self.assertEqual(result["tablas_actualizadas"], 1)
        self.assertFalse(result["archivo_almacenado"])


if __name__ == "__main__":
    import unittest

    unittest.main()
