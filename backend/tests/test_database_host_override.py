from unittest import TestCase

from sqlalchemy.engine import make_url

from app.db.config import DatabaseSettings


class DatabaseHostOverrideTests(TestCase):
    def test_without_override_keeps_direct_local_connection(self):
        original = "postgresql+psycopg://demo:example@localhost:5432/test_etl"
        settings = DatabaseSettings(
            _env_file=None, database_url=original, database_host_override=None,
        )
        self.assertEqual(settings.require_database_url(), original)

    def test_changes_only_host_and_preserves_encoded_password_and_options(self):
        original = make_url(
            "postgresql+psycopg://demo:p%40ss%24%2Fword@127.0.0.1:5433/test_etl?sslmode=prefer"
        )
        settings = DatabaseSettings(
            _env_file=None,
            database_url=original.render_as_string(hide_password=False),
            database_host_override="host.docker.internal",
        )
        actual = make_url(settings.require_database_url())
        self.assertEqual(actual, original.set(host="host.docker.internal"))
        self.assertEqual(actual.password, original.password)

    def test_override_does_not_invent_missing_credentials(self):
        settings = DatabaseSettings(
            _env_file=None, database_url=None,
            database_host_override="host.docker.internal",
        )
        with self.assertRaises(RuntimeError):
            settings.require_database_url()
