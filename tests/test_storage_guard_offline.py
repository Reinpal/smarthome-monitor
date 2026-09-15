"""Storage privacy regression tests: fake commands/directories, no mounts/sudo."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StorageGuardOfflineTests(unittest.TestCase):
    def run_guard(self, mode):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            mount = temp / "fictional-mount"
            (mount / "smarthome-data/prometheus").mkdir(parents=True)
            scripts = {
                "findmnt": '''#!/bin/sh
case "$*" in
  *--fstab*)
    case "$TEST_MODE" in
      missing-fstab) exit 1 ;;
      path-source) echo /dev/fictional ;;
      ambiguous) printf 'UUID=1111-2222\\nUUID=3333-4444\\n' ;;
      *) echo UUID=11111111-2222-4333-8444-555555555555 ;;
    esac ;;
  *OPTIONS*)
    if [ "$TEST_MODE" = readonly ]; then echo ro; else echo rw; fi ;;
  *)
    if [ "$TEST_MODE" = wrong-drive ]; then echo FICTIONAL-SENSITIVE-SENTINEL
    else echo 11111111-2222-4333-8444-555555555555; fi ;;
esac
''',
                "mountpoint": '#!/bin/sh\n[ "$TEST_MODE" != missing-mount ]\n',
                "stat": '#!/bin/sh\nif [ "$TEST_MODE" = missing-device ] && [ "$1" = -Lc ]; then exit 1; fi\necho 42\n',
            }
            for name, script in scripts.items():
                target = temp / name
                target.write_text(script)
                target.chmod(0o755)
            guard = temp / "guard"
            source = (ROOT / "deploy/check-smarthome-storage").read_text()
            guard.write_text(source.replace("mount_path=/mnt/external", f"mount_path='{mount}'"))
            env = {**os.environ, "PATH": f"{temp}:{os.environ['PATH']}", "TEST_MODE": mode}
            return subprocess.run(["sh", str(guard)], env=env, capture_output=True, text=True)

    def test_correct_existing_fstab_drive_accepted(self):
        result = self.run_guard("correct")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_ambiguous_wrong_and_readonly_fail_closed_without_identifier(self):
        for mode in ("missing-fstab", "path-source", "ambiguous", "missing-mount", "wrong-drive", "readonly", "missing-device"):
            with self.subTest(mode=mode):
                result = self.run_guard(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("FICTIONAL-SENSITIVE-SENTINEL", result.stdout + result.stderr)
                self.assertIn("SmartHome storage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
