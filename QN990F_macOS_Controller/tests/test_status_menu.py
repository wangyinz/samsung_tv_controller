"""Native, noninteractive menu checks on macOS, isolated from installed state."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "darwin", "Native AppKit tests require macOS")
class StatusMenuTests(unittest.TestCase):
    def test_native_menu_modes_and_volume_submission(self):
        tests = Path(__file__).parent
        with tempfile.TemporaryDirectory(prefix="tv-status-menu-test-") as directory:
            compiled = Path(directory) / "StatusMenu.scpt"
            result = subprocess.run(
                ["/usr/bin/osacompile", "-o", str(compiled), str(tests.parent / "StatusMenu.applescript")],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(
                ["/usr/bin/osascript", str(tests / "Test-StatusMenu.applescript"), str(compiled), directory],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PASS:", result.stdout)
            print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
