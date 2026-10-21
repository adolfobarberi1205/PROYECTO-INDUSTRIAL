from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.services.additional_files_service import (
    obtener_ruta_kettle_temporal,
)


class KettleUploadResolutionTests(TestCase):
    def test_returns_uploaded_kettle_path(self) -> None:
        upload_id = "a" * 32

        with TemporaryDirectory() as directory:
            kettle_path = (
                Path(directory)
                / "complementos"
                / upload_id
                / "kettle.properties"
            )
            kettle_path.parent.mkdir(parents=True)
            kettle_path.write_text(
                "esquema_dwh_dest=DWH_PRUEBA\n",
                encoding="utf-8",
            )

            result = obtener_ruta_kettle_temporal(
                id_carga=upload_id,
                directorio_temporal=directory,
            )

            self.assertEqual(result, kettle_path)

    def test_returns_none_when_kettle_was_not_uploaded(self) -> None:
        with TemporaryDirectory() as directory:
            result = obtener_ruta_kettle_temporal(
                id_carga="b" * 32,
                directorio_temporal=directory,
            )

            self.assertIsNone(result)

    def test_rejects_invalid_upload_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "identificador"):
            obtener_ruta_kettle_temporal(
                id_carga="invalid",
                directorio_temporal=".",
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
