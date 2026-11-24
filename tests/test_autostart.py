from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AutoStartTests(unittest.TestCase):
    def test_app_has_per_user_registry_startup_toggle(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('STARTUP_RUN_KEY = r"Software\\Microsoft\\Windows\\CurrentVersion\\Run"', app)
        self.assertIn("def set_windows_startup(enabled: bool)", app)
        self.assertIn("winreg.HKEY_CURRENT_USER", app)
        self.assertIn('"start_with_windows": requested_startup', app)
        self.assertIn('text="Start automatically when I sign in (minimised to tray)"', app)

    def test_startup_command_runs_hidden_vbs_with_hidden_start_flag(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('"{launcher}" --start-hidden', app)
        vbs = (ROOT / "launch_hidden.vbs").read_text(encoding="utf-8").lower()
        self.assertIn('lcase(wscript.arguments(0)) = "--start-hidden"', vbs)
        self.assertIn('extraargs = " --start-hidden"', vbs)

    def test_hidden_batch_worker_forwards_start_hidden_to_python(self):
        batch = (ROOT / "start.bat").read_text(encoding="utf-8")
        self.assertIn('%PYTHON_CMD% -u app.py %~2', batch)
        self.assertIn('"--worker"', batch.lower())

    def test_auto_started_instance_hides_to_tray(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('if "--start-hidden" in sys.argv[1:]:', app)
        self.assertIn('self.root.after(900, self.hide_to_tray)', app)


if __name__ == "__main__":
    unittest.main()
