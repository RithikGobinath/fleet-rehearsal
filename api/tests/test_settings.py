import unittest
from pathlib import Path

from fleet.settings import LocalSettings


class LocalSettingsTests(unittest.TestCase):
    def test_local_mode_requires_explicit_opt_in(self):
        for env in ({}, {"FLEET_DEMO_MODE": "hosted"}):
            with self.subTest(env=env), self.assertRaisesRegex(RuntimeError, "FLEET_DEMO_MODE=local"):
                LocalSettings.from_env(env)

    def test_local_mode_requires_a_database(self):
        with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
            LocalSettings.from_env({"FLEET_DEMO_MODE": "local", "DATABASE_URL": " "})

    def test_fixture_can_be_selected_without_printing_connection_details(self):
        settings = LocalSettings.from_env({
            "FLEET_DEMO_MODE": "local",
            "DATABASE_URL": "postgresql://fleet@db/fleet",
            "FLEET_FIXTURE": "fixtures/demo.json",
        })
        self.assertEqual(settings.fixture_path, Path("fixtures/demo.json"))
        self.assertNotIn(settings.database_url, repr(settings))
