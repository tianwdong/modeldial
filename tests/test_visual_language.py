import unittest
from pathlib import Path


class VisualLanguageSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parent.parent
        cls.typography = (root / "Sources/Theme/Typography.swift").read_text(encoding="utf-8")
        cls.colors = (root / "Sources/Theme/Colors.swift").read_text(encoding="utf-8")
        cls.animations = (root / "Sources/Theme/Animations.swift").read_text(encoding="utf-8")
        cls.island_root = (root / "Sources/Views/IslandRootView.swift").read_text(encoding="utf-8")
        cls.expanded = (root / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        cls.settings = (root / "Sources/Views/SettingsView.swift").read_text(encoding="utf-8")
        cls.interactions_path = root / "Sources/Theme/InteractionStyles.swift"
        cls.settings_button = (root / "Sources/Views/SettingsButton.swift").read_text(encoding="utf-8")
        cls.quit_button = (root / "Sources/Views/QuitButton.swift").read_text(encoding="utf-8")
        cls.restart_button = (root / "Sources/Views/RestartScanButton.swift").read_text(encoding="utf-8")
        cls.app = (root / "Sources/App.swift").read_text(encoding="utf-8")

    def test_theme_exposes_locked_type_scale(self) -> None:
        for token in (
            "static let heroModel",
            "static let pageTitle",
            "static let sectionTitle",
            "static let metricValue",
            "static let tableValue",
            "static let scoreValue",
        ):
            self.assertIn(token, self.typography)
        self.assertNotIn("size: 7", self.typography)
        self.assertNotIn("size: 8", self.typography)
        self.assertNotIn("size: 9", self.typography)
        self.assertIn("static let heroModel = Font.system(size: 24", self.typography)
        self.assertIn(
            "static let heroDecision = Font.system(size: 19, weight: .semibold)",
            self.typography,
        )
        self.assertIn("static let pageTitle = Font.system(size: 24", self.typography)
        self.assertIn("static let sectionTitle = Font.system(size: 16", self.typography)
        self.assertIn("static let metricValue = Font.system(size: 16", self.typography)
        self.assertIn("static let tableValue = Font.system(size: 16", self.typography)
        self.assertIn("static let bigNumber = Font.system(size: 40", self.typography)

    def test_theme_exposes_shared_eight_point_layout_rhythm(self) -> None:
        for token in (
            "enum LayoutRhythm",
            "static let compact: CGFloat = 8",
            "static let standard: CGFloat = 16",
            "static let section: CGFloat = 24",
            "static let large: CGFloat = 32",
        ):
            self.assertIn(token, self.typography)

    def test_theme_exposes_fixed_text_and_surface_ladder(self) -> None:
        for token in (
            "enum IslandVisual",
            "static let primaryText",
            "static let secondaryText",
            "static let tertiaryText",
            "static let hintText",
            "static let hairline",
            "static let surfaceSubtle",
            "static let surfaceRaised",
            "static let workspaceSurface",
            "static let workspaceBorder",
            "static let floatingSurface",
        ):
            self.assertIn(token, self.colors)

    def test_theme_exposes_three_layer_native_material_system(self) -> None:
        for token in (
            "static let shellSurface",
            "static let summarySurface",
            "static let evidenceSurface",
            "static let interactionSurface",
            "static let contentTopHighlight",
        ):
            self.assertIn(token, self.colors)
        self.assertIn(
            "static let panel = Color(red: 7/255, green: 9/255, blue: 11/255)",
            self.colors,
        )
        self.assertNotIn("static let panel = canvas", self.colors)
        self.assertIn(
            "static let summarySurface = Color(red: 13/255, green: 17/255, blue: 22/255)",
            self.colors,
        )
        self.assertIn(
            "static let evidenceSurface = Color(red: 9/255, green: 12/255, blue: 16/255)",
            self.colors,
        )

    def test_theme_separates_regular_and_emphasized_ranking_values(self) -> None:
        self.assertIn(
            "static let rankingHeader = Font.system(size: 12, weight: .medium)",
            self.typography,
        )
        self.assertIn(
            "static let rankingModel = Font.system(size: 14, weight: .medium)",
            self.typography,
        )
        self.assertIn(
            "static let rankingModelEmphasis = Font.system(size: 14, weight: .semibold)",
            self.typography,
        )
        self.assertIn(
            "static let rankingValue = Font.system(size: 13, weight: .regular, design: .monospaced)",
            self.typography,
        )
        self.assertIn(
            "static let rankingValueEmphasis = Font.system(size: 13, weight: .semibold, design: .monospaced)",
            self.typography,
        )

    def test_theme_separates_interaction_color_from_status_colors(self) -> None:
        for token in (
            "static let interaction = Color(red: 134/255, green: 168/255, blue: 196/255)",
            "static let liveTeal = Color(red: 67/255, green: 213/255, blue: 150/255)",
            "static let alertAmber = Color(red: 220/255, green: 166/255, blue: 74/255)",
            "static let alertRed = Color(red: 230/255, green: 98/255, blue: 104/255)",
            "static let interactionText = IslandColor.interaction",
            "static let selectedBorder = IslandColor.interaction.opacity(0.42)",
        ):
            self.assertIn(token, self.colors)
        self.assertNotIn("selectedSurface = IslandColor.liveTeal", self.colors)
        self.assertNotIn("selectedBorder = IslandColor.liveTeal", self.colors)

    def test_theme_exposes_reduced_motion_compatible_control_timing(self) -> None:
        self.assertIn("static let controlSelection", self.animations)
        self.assertIn("duration: 0.22", self.animations)
        self.assertIn("static let interactionFeedback", self.animations)
        source = self.interactions_path.read_text(encoding="utf-8")
        self.assertIn("@Environment(\\.accessibilityReduceMotion)", source)
        self.assertIn("reduceMotion ? nil : .interactionFeedback", source)

    def test_small_text_uses_one_explicit_product_scale(self) -> None:
        for token in (
            "static let rowTitle = Font.system(size: 14, weight: .medium)",
            "static let tabLabel = Font.system(size: 13, weight: .semibold)",
            "static let button = Font.system(size: 13, weight: .semibold)",
            "static let label = Font.system(size: 12, weight: .medium)",
            "static let micro = Font.system(size: 11, weight: .medium)",
            "static let sectionLabel = Font.system(size: 12, weight: .semibold)",
        ):
            self.assertIn(token, self.typography)
        self.assertNotIn("Font.caption", self.typography)
        self.assertNotIn("Font.body", self.typography)

    def test_product_views_do_not_bypass_the_shared_font_scale(self) -> None:
        for source in (self.expanded, self.settings):
            self.assertNotIn(".font(.system(size:", source)

    def test_product_views_do_not_use_one_off_card_radii(self) -> None:
        for source in (self.expanded, self.settings):
            for radius in ("cornerRadius: 7", "cornerRadius: 18", "cornerRadius: 20"):
                self.assertNotIn(radius, source)

    def test_theme_exposes_shared_radius_scale(self) -> None:
        for token in (
            "enum IslandRadius",
            "static let control: CGFloat = 8",
            "static let card: CGFloat = 12",
            "static let modal: CGFloat = 16",
            "static let panel: CGFloat = 24",
        ):
            self.assertIn(token, self.typography)

    def test_root_and_expanded_surfaces_honor_transparency_and_contrast(self) -> None:
        for source in (self.island_root, self.expanded):
            self.assertIn("@Environment(\\.accessibilityReduceTransparency)", source)
            self.assertIn("@Environment(\\.colorSchemeContrast)", source)
        self.assertNotIn(".fill(.ultraThinMaterial)", self.island_root)
        self.assertIn("IslandVisual.panelBackground(reduceTransparency:", self.expanded)
        self.assertIn("IslandVisual.border(increasedContrast:", self.island_root)
        self.assertIn("static func panelBackground(reduceTransparency: Bool)", self.colors)
        self.assertIn("static func border(increasedContrast: Bool)", self.colors)

    def test_shared_action_style_covers_interaction_states(self) -> None:
        self.assertTrue(self.interactions_path.exists())
        source = self.interactions_path.read_text(encoding="utf-8")
        for token in (
            "struct IslandActionButtonStyle",
            "enum IslandActionKind",
            "@Environment(\\.isEnabled)",
            "@State private var hovered",
            "configuration.isPressed",
            "NSCursor.pointingHand",
            "NSCursor.pop()",
            "struct IslandPointerHoverModifier",
            "func islandPointerOnHover",
        ):
            self.assertIn(token, source)
        self.assertIn("RoundedRectangle(cornerRadius: IslandRadius.control", source)
        self.assertIn("IslandColor.interaction", source)
        self.assertNotIn(".background(Capsule()", source)

    def test_compact_island_does_not_scale_against_the_outline_morph(self) -> None:
        source = self.interactions_path.read_text(encoding="utf-8")
        self.assertNotIn("struct CompactIslandButtonStyle: ButtonStyle", source)
        self.assertNotIn("hovered ? 1.012", source)
        self.assertIn(".buttonStyle(.plain)", self.island_root)

    def test_icon_actions_have_tooltips_and_pointer_feedback(self) -> None:
        self.assertIn('.help(L10n.tr("打开设置"))', self.settings_button)
        self.assertIn("IslandIconButtonStyle", self.settings_button)
        self.assertIn('.help(L10n.tr("退出 modeldial"))', self.quit_button)
        self.assertIn("IslandIconButtonStyle", self.quit_button)
        self.assertIn('.help(L10n.tr("放弃当前进度并重新扫描"))', self.restart_button)
        self.assertIn("IslandActionButtonStyle", self.restart_button)
        self.assertNotIn("RestartPillButtonStyle", self.restart_button)
        source = self.interactions_path.read_text(encoding="utf-8")
        self.assertIn("struct IslandIconButtonStyle", source)
        self.assertIn("RoundedRectangle(cornerRadius: IslandRadius.control", source)
        self.assertIn("NSCursor.pointingHand", source)
        self.assertNotIn("Circle()", self.settings_button)
        self.assertNotIn("Circle()", self.quit_button)

    def test_app_locks_native_alerts_to_dark_appearance(self) -> None:
        self.assertIn("NSApp.appearance = NSAppearance(named: .darkAqua)", self.app)

    def test_confirmation_actions_use_attached_swiftui_alerts(self) -> None:
        for source, state_name in (
            (self.restart_button, "showsRestartConfirmation"),
            (self.quit_button, "showsQuitConfirmation"),
        ):
            self.assertIn(f"@State private var {state_name} = false", source)
            self.assertIn(".alert(", source)
            self.assertIn(f"isPresented: ${state_name}", source)
            self.assertIn('Button(L10n.tr("取消"), role: .cancel)', source)
            self.assertNotIn("NSAlert", source)
            self.assertNotIn("runModal", source)


if __name__ == "__main__":
    unittest.main()
