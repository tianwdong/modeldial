from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class ReleaseSbomTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent.parent
        self.candidate = self.root / "build" / "modeldial-candidate.app"
        self.generator = self.root / "build-support" / "generate-sbom.py"
        self.validator = self.root / "build-support" / "verify-sbom.py"

    def test_generator_and_validator_use_real_candidate_files(self) -> None:
        if not self.candidate.is_dir():
            self.skipTest("requires a locally built modeldial-candidate.app")
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            bundle = temporary_root / "modeldial-candidate.app"
            shutil.copytree(self.candidate, bundle)
            legal_dir = bundle / "Contents" / "Resources" / "Legal"
            legal_dir.mkdir(parents=True, exist_ok=True)
            for source in (self.root / "Resources" / "Legal").glob("*.txt"):
                shutil.copy2(source, legal_dir / source.name)
            sbom = temporary_root / "modeldial-0.1.0-preview.1-sbom.spdx.json"
            inventory = temporary_root / "inventory.json"
            subprocess.run(
                [
                    sys.executable,
                    str(self.generator),
                    "--bundle",
                    str(bundle),
                    "--output",
                    str(sbom),
                    "--release-label",
                    "v0.1.0-preview.1",
                    "--inventory-output",
                    str(inventory),
                ],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
            )
            document = json.loads(sbom.read_text(encoding="utf-8"))
            self.assertEqual(document["spdxVersion"], "SPDX-2.3")
            self.assertGreater(len(document["files"]), 10)
            file_hashes = []
            for entry in document["files"]:
                path = bundle / entry["fileName"]
                file_hashes.append(hashlib.sha1(path.read_bytes()).hexdigest())
            expected_verification = hashlib.sha1(
                "".join(sorted(file_hashes)).encode("ascii")
            ).hexdigest()
            app_package = next(
                package for package in document["packages"] if package["name"] == "ModelDial"
            )
            self.assertEqual(
                app_package["packageVerificationCode"][
                    "packageVerificationCodeValue"
                ],
                expected_verification,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(self.validator),
                    "--bundle",
                    str(bundle),
                    "--sbom",
                    str(sbom),
                ],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
            )
            backend = bundle / "Contents" / "Resources" / "Backend" / "Runtime" / "modeldial-backend"
            with backend.open("ab") as handle:
                handle.write(b"tampered")
            failed = subprocess.run(
                [
                    sys.executable,
                    str(self.validator),
                    "--bundle",
                    str(bundle),
                    "--sbom",
                    str(sbom),
                ],
                cwd=self.root,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("checksum mismatch", failed.stderr)


if __name__ == "__main__":
    unittest.main()
