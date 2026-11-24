from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LauncherFileTests(unittest.TestCase):
    def test_batch_does_not_require_windowed_python_launcher(self):
        batch = (ROOT / "start.bat").read_text(encoding="utf-8").lower()
        self.assertNotIn("where pyw", batch)
        self.assertIn("where py", batch)
        self.assertIn("where python", batch)
        self.assertIn("-u app.py", batch)

    def test_batch_has_visible_diagnostics_on_startup_failure(self):
        batch = (ROOT / "start.bat").read_text(encoding="utf-8").lower()
        self.assertIn("launcher.log", batch)
        self.assertIn("notepad.exe", batch)
        self.assertIn(":dependency_error", batch)
        self.assertIn(":app_error", batch)

    def test_vbs_calls_quoted_batch_path(self):
        vbs = (ROOT / "launch_hidden.vbs").read_text(encoding="utf-8").lower()
        self.assertIn('chr(34) & batchfile & chr(34)', vbs)
        self.assertIn('" --worker"', vbs)
        self.assertIn('shell.run command, 0, false', vbs)


if __name__ == "__main__":
    unittest.main()
