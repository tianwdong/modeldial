from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GlanceStateContractTest(unittest.TestCase):
    def compile_and_run_swift(
        self,
        sources: list[str],
        *,
        executable_name: str,
        success_marker: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / executable_name
            compile_result = subprocess.run(
                ["swiftc", *sources, "-o", str(executable)],
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
            self.assertIn(success_marker, run_result.stdout)

    def test_glance_resolver_behavior_contract_is_executable(self) -> None:
        self.compile_and_run_swift(
            [
                "Sources/Model/AppLanguageStore.swift",
                "Sources/Localization/L10n.swift",
                "Sources/Model/GlanceState.swift",
                "tests/swift/GlanceStateResolverTests.swift",
            ],
            executable_name="glance-state-resolver",
            success_marker="GlanceStateResolver tests passed",
        )

    def test_resolver_has_no_ui_or_timer_dependencies(self) -> None:
        resolver = (ROOT / "Sources/Model/GlanceState.swift").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("Timer(", resolver)
        self.assertNotIn("AppKit", resolver)

    def test_startup_maintenance_coordinator_is_executable(self) -> None:
        self.compile_and_run_swift(
            [
                "Sources/Model/LocalEncryptedSecretStore.swift",
                "Sources/Model/SelectionModels.swift",
                "Sources/Model/StartupLoadCoordinator.swift",
                "tests/swift/StartupLoadCoordinatorTests.swift",
            ],
            executable_name="startup-load-coordinator",
            success_marker="Startup load coordinator tests passed",
        )

    def test_runtime_event_reducer_preserves_authoritative_failure_state(self) -> None:
        self.compile_and_run_swift(
            [
                "Sources/Model/LocalEncryptedSecretStore.swift",
                "Sources/Model/SelectionModels.swift",
                "Sources/Model/RuntimeEventReducer.swift",
                "tests/swift/RuntimeEventReducerTests.swift",
            ],
            executable_name="runtime-event-reducer",
            success_marker="Runtime event reducer tests passed",
        )

    def test_v2_recommendation_is_the_primary_glance_projection(self) -> None:
        store = (ROOT / "Sources/Model/SelectionStore.swift").read_text(
            encoding="utf-8"
        )
        recommendation = store.split(
            "private func recommendationSnapshot()", 1
        )[1].split("private func runtimeLifecycle", 1)[0]

        self.assertIn(
            "guard (snapshot?.runtime.enabledTargetCount ?? 0) > 0 else { return nil }",
            recommendation,
        )
        self.assertIn("if snapshot?.recommendationPortfolioV2 != nil", recommendation)
        self.assertIn("return radarRecommendationSnapshot()", recommendation)
        self.assertLess(
            recommendation.index("recommendationPortfolioV2"),
            recommendation.index("dashboard.bestCombination"),
        )

    def test_control_navigation_and_lifecycle_keep_minimal_wiring(self) -> None:
        store = (ROOT / "Sources/Model/SelectionStore.swift").read_text(
            encoding="utf-8"
        )
        app = (ROOT / "Sources/App.swift").read_text(encoding="utf-8")
        resolve_glance = store.split("private func resolveGlance", 1)[1].split(
            "private func runtimeSnapshotState", 1
        )[0]
        consume_destination = store.split(
            "func consumeExpandedDestination() -> GlanceDestination", 1
        )[1].split("func setGlanceActuallyVisible", 1)[0]

        self.assertIn("GlanceStateResolver.stoppingPresentation", resolve_glance)
        self.assertIn("GlanceStateResolver.pausingPresentation", resolve_glance)
        self.assertIn("defer { expandedDestination = nil }", consume_destination)
        self.assertIn("NSWorkspace.willSleepNotification", app)
        self.assertIn("NSWorkspace.didWakeNotification", app)
        self.assertIn("NSWorkspace.sessionDidBecomeActiveNotification", app)
        self.assertIn("NSApplication.didBecomeActiveNotification", app)
        self.assertIn("AppSessionStore.shared.resumeGlanceBoundaryRefresh()", app)


if __name__ == "__main__":
    unittest.main()
