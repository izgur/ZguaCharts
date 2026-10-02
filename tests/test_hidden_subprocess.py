import unittest
from unittest.mock import patch

from services import hidden_subprocess


class HiddenSubprocessTests(unittest.TestCase):
    def test_windows_flags_preserve_process_group(self):
        with patch.object(hidden_subprocess.os, "name", "nt"), patch.object(
            hidden_subprocess._subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True
        ), patch.object(hidden_subprocess._subprocess, "run") as run:
            hidden_subprocess.run(["node", "--version"], creationflags=512, capture_output=True)
            self.assertEqual(run.call_args[1]["creationflags"], 0x08000000 | 512)
            self.assertTrue(run.call_args[1]["capture_output"])

    def test_other_platforms_leave_options_unchanged(self):
        with patch.object(hidden_subprocess.os, "name", "posix"), patch.object(
            hidden_subprocess._subprocess, "Popen"
        ) as popen:
            hidden_subprocess.Popen(["node", "--version"], stdout=123)
            self.assertNotIn("creationflags", popen.call_args[1])
            self.assertEqual(popen.call_args[1]["stdout"], 123)


if __name__ == "__main__":
    unittest.main()
