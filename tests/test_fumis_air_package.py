import ast
import json
import tempfile
import unittest
from pathlib import Path


INTEGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components" / "fumis_air"
)


def _missing_platform_modules(platforms, integration_path):
    return [
        platform
        for platform in platforms
        if not (integration_path / f"{platform}.py").is_file()
    ]


class FumisAirPackageTest(unittest.TestCase):
    def test_manifest_has_new_domain_and_supported_advertisements(self):
        manifest = json.loads(
            (INTEGRATION_PATH / "manifest.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["domain"], "fumis_air")
        self.assertEqual(manifest["name"], "Fumis AIR")
        self.assertEqual(
            [
                (matcher["local_name"], matcher["connectable"], matcher["service_uuid"])
                for matcher in manifest["bluetooth"]
            ],
            [
                ("Fumis AIR", True, "0000fff0-0000-1000-8000-00805f9b34fb"),
                ("Senso", True, "0000fff0-0000-1000-8000-00805f9b34fb"),
                ("AirMaster", True, "0000fff0-0000-1000-8000-00805f9b34fb"),
            ],
        )

    def test_all_declared_platforms_have_modules(self):
        init_path = INTEGRATION_PATH / "__init__.py"
        module = ast.parse(init_path.read_text(encoding="utf-8"))
        platforms = next(
            ast.literal_eval(statement.value)
            for statement in module.body
            if isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "PLATFORMS"
                for target in statement.targets
            )
        )

        self.assertEqual(_missing_platform_modules(platforms, INTEGRATION_PATH), [])

    def test_controller_profiles_are_bundled(self):
        profiles = INTEGRATION_PATH / "profiles"
        self.assertTrue((profiles / "common.yaml").is_file())
        self.assertTrue((profiles / "senso.yaml").is_file())
        self.assertTrue((profiles / "fumis_air.yaml").is_file())

    def test_detects_an_omitted_platform_module(self):
        with tempfile.TemporaryDirectory() as directory:
            integration_path = Path(directory)
            (integration_path / "sensor.py").touch()

            self.assertEqual(
                _missing_platform_modules(
                    ("sensor", "binary_sensor"), integration_path
                ),
                ["binary_sensor"],
            )


if __name__ == "__main__":
    unittest.main()