from __future__ import annotations

import plistlib
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class UpdaterControllerContractTest(unittest.TestCase):
    def test_update_configuration_requires_https_and_a_valid_ed25519_public_key(self) -> None:
        source = ROOT / "Sources" / "Update" / "UpdateConfiguration.swift"
        self.assertTrue(source.is_file())

        swift_test = """
import Foundation

@main
struct UpdateConfigurationTests {
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) {
        guard condition() else {
            fputs("FAIL: \\(message)\\n", stderr)
            exit(1)
        }
    }

    static func main() {
        let publicKey = Data(repeating: 7, count: 32).base64EncodedString()
        let valid = UpdateConfiguration(infoDictionary: [
            "SUFeedURL": "  https://updates.example.com/macos/stable/appcast.xml  ",
            "SUPublicEDKey": "  \\(publicKey)  ",
        ])
        require(valid.isConfigured, "valid HTTPS feed and Ed25519 key should configure updates")

        require(!UpdateConfiguration(infoDictionary: [
            "SUFeedURL": "http://updates.example.com/appcast.xml",
            "SUPublicEDKey": publicKey,
        ]).isConfigured, "HTTP feeds must be rejected")

        require(!UpdateConfiguration(infoDictionary: [
            "SUFeedURL": "https://updates.example.com/appcast.xml",
        ]).isConfigured, "the public key is required")

        require(!UpdateConfiguration(infoDictionary: [
            "SUFeedURL": "https://user:password@updates.example.com/appcast.xml",
            "SUPublicEDKey": publicKey,
        ]).isConfigured, "credentials in feed URLs must be rejected")

        let shortKey = Data(repeating: 7, count: 31).base64EncodedString()
        require(!UpdateConfiguration(infoDictionary: [
            "SUFeedURL": "https://updates.example.com/appcast.xml",
            "SUPublicEDKey": shortKey,
        ]).isConfigured, "the decoded Ed25519 public key must be 32 bytes")

        print("UpdateConfiguration tests passed")
    }
}
"""
        with tempfile.TemporaryDirectory() as temporary:
            test_path = Path(temporary) / "UpdateConfigurationTests.swift"
            executable = Path(temporary) / "update-configuration-tests"
            test_path.write_text(swift_test, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temporary) / "module-cache"),
                    str(source),
                    str(test_path),
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertIn("UpdateConfiguration tests passed", run_result.stdout)

    def test_controller_starts_only_after_launch_and_only_when_configured(self) -> None:
        controller = (
            ROOT / "Sources" / "Update" / "UpdaterController.swift"
        ).read_text(encoding="utf-8")
        app = (ROOT / "Sources" / "App.swift").read_text(encoding="utf-8")

        self.assertIn("SPUStandardUpdaterController(", controller)
        self.assertIn("startingUpdater: false", controller)
        self.assertIn("guard configuration.isConfigured, !hasStarted", controller)
        self.assertIn("standardUpdaterController.startUpdater()", controller)
        self.assertIn("UpdaterController.shared.startIfConfigured()", app)
        self.assertIn("updaterDelegate: updateCheckObserver", controller)
        self.assertIn("updater.checkForUpdateInformation()", controller)
        self.assertIn("UpdateCheckObserver", controller)
        self.assertIn("@Published private(set) var updateCheckState", controller)
        self.assertIn("standardUpdaterController.checkForUpdates(nil)", controller)

    def test_update_check_presentation_is_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "update-check-presenter-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temporary) / "module-cache"),
                    "Sources/Model/AppLanguageStore.swift",
                    "Sources/Localization/L10n.swift",
                    "Sources/Update/UpdateCheckPresentation.swift",
                    "tests/swift/UpdateCheckPresenterTests.swift",
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertIn("Update check presenter tests passed", run_result.stdout)

    def test_sparkle_preferences_keep_permission_and_privacy_boundaries(self) -> None:
        info = plistlib.loads((ROOT / "Resources" / "Info.plist").read_bytes())

        self.assertIs(info["SUAutomaticallyUpdate"], False)
        self.assertIs(info["SUSendProfileInfo"], False)
        self.assertNotIn("SUEnableAutomaticChecks", info)
        self.assertEqual(
            info["SUFeedURL"],
            "$(MODELDIAL_SU_FEED_URL)",
        )
        self.assertEqual(info["SUPublicEDKey"], "$(MODELDIAL_SU_PUBLIC_ED_KEY)")
        self.assertEqual(info["ModelDialSourceCommit"], "$(MODELDIAL_SOURCE_COMMIT)")

        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn(
            'UPDATE_FEED_URL="${MODELDIAL_UPDATE_FEED_URL:-}"',
            build,
        )
        self.assertIn(
            'UPDATE_PUBLIC_ED_KEY="${MODELDIAL_UPDATE_PUBLIC_ED_KEY:-}"',
            build,
        )
        self.assertIn(
            "MODELDIAL_UPDATE_FEED_URL and MODELDIAL_UPDATE_PUBLIC_ED_KEY must be provided together.",
            build,
        )

    def test_settings_expose_version_check_and_sparkle_owned_preferences(self) -> None:
        settings = (ROOT / "Sources" / "Views" / "SettingsView.swift").read_text(
            encoding="utf-8"
        )

        self.assertIn("@ObservedObject private var updater = UpdaterController.shared", settings)
        self.assertIn("case .updates", settings)
        self.assertIn('case .updates: return "软件更新"', settings)
        self.assertIn("private var softwareUpdateSection", settings)
        self.assertIn("private var softwareUpdateContent", settings)
        self.assertIn("L10n.Update.versionBuild", settings)
        self.assertIn("updater.checkForUpdates()", settings)
        self.assertIn("UpdateCheckPresenter.presentation(", settings)
        self.assertIn("formRow(L10n.Update.status)", settings)
        self.assertIn("updater.setAutomaticallyChecksForUpdates", settings)
        self.assertIn("updater.setAutomaticallyDownloadsUpdates", settings)
        self.assertIn(
            ".disabled(!updater.canCheckForUpdates || updater.updateCheckState.isChecking)",
            settings,
        )
        self.assertIn("!updater.isConfigured || !updater.allowsAutomaticUpdates", settings)


if __name__ == "__main__":
    unittest.main()
