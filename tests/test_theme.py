"""Theme resolution with temporary configs; the real Herdr config is never read."""

import tempfile
import unittest
from pathlib import Path

from herdr_agent_rename.theme import (
    PALETTES,
    TOKENS,
    canonical_name,
    config_path,
    load_theme,
    parse_color,
    theme_from_config,
)


class PaletteTests(unittest.TestCase):
    def test_every_builtin_palette_has_all_tokens(self):
        self.assertEqual(len(PALETTES), 18)
        for name, palette in PALETTES.items():
            self.assertEqual(set(palette), set(TOKENS), name)

    def test_known_values(self):
        self.assertEqual(PALETTES["catppuccin"]["panel_bg"], "#181825")
        self.assertEqual(PALETTES["dracula"]["accent"], "#bd93f9")
        self.assertIsNone(PALETTES["terminal"]["panel_bg"])
        self.assertEqual(PALETTES["terminal"]["accent"], "ansiblue")

    def test_styles_map_native_tokens(self):
        styles = theme_from_config({}).styles()
        self.assertEqual(styles["panel"], "fg:#cdd6f4 bg:#181825")
        self.assertEqual(styles["title"], "fg:#cdd6f4 bg:#181825 bold")
        self.assertEqual(styles["input"], "fg:#cdd6f4 bg:#313244")
        self.assertEqual(styles["primary"], "fg:#181825 bg:#89b4fa bold")
        self.assertEqual(styles["secondary"], "fg:#cdd6f4 bg:#313244 bold")

    def test_primary_contrast_falls_back_to_surface_dim_when_panel_is_reset(self):
        styles = theme_from_config({"theme": {"name": "terminal"}}).styles()
        self.assertEqual(styles["primary"], "fg:ansibrightblack bg:ansiblue bold")
        self.assertEqual(styles["panel"], "")


class ResolutionTests(unittest.TestCase):
    def test_default_and_unknown_names_fall_back_to_catppuccin(self):
        self.assertEqual(theme_from_config(None).name, "catppuccin")
        self.assertEqual(theme_from_config({"theme": {"name": "nope"}}).name, "catppuccin")
        self.assertEqual(theme_from_config({"theme": {"name": 3}}).name, "catppuccin")
        self.assertEqual(theme_from_config({"theme": "x"}).name, "catppuccin")

    def test_aliases_and_case(self):
        self.assertEqual(canonical_name("Tokyo Night"), "tokyo-night")
        self.assertEqual(theme_from_config({"theme": {"name": "Latte"}}).name, "catppuccin-latte")
        self.assertEqual(theme_from_config({"theme": {"name": "rose_pine"}}).name, "rose-pine")

    def test_custom_overrides_apply_after_palette(self):
        theme = theme_from_config(
            {"theme": {"name": "dracula", "custom": {"accent": "#abc", "panel_bg": "reset", "bogus": "red", "text": 5}}}
        )
        self.assertEqual(theme.color("accent"), "#aabbcc")
        self.assertIsNone(theme.color("panel_bg"))
        self.assertEqual(theme.color("text"), "#f8f8f2")

    def test_legacy_ui_accent(self):
        self.assertEqual(theme_from_config({"ui": {"accent": "red"}}).color("accent"), "ansired")
        self.assertEqual(theme_from_config({"ui": {"accent": "cyan"}}).color("accent"), "#89b4fa")
        both = {"ui": {"accent": "red"}, "theme": {"custom": {"accent": "#000000"}}}
        self.assertEqual(theme_from_config(both).color("accent"), "#000000")

    def test_auto_switch_assumes_dark(self):
        light = {"theme": {"name": "gruvbox-light", "auto_switch": True}}
        self.assertEqual(theme_from_config(light).name, "gruvbox")
        explicit = {"theme": {"name": "nord", "auto_switch": True, "dark_name": "dracula",
                              "custom": {"dark": {"accent": "#111111"}, "light": {"accent": "#222222"}}}}
        theme = theme_from_config(explicit)
        self.assertEqual((theme.name, theme.color("accent")), ("dracula", "#111111"))
        off = {"theme": {"name": "gruvbox-light", "auto_switch": False}}
        self.assertEqual(theme_from_config(off).name, "gruvbox-light")

    def test_color_strings(self):
        self.assertEqual(parse_color("#ABCDEF"), "#abcdef")
        self.assertEqual(parse_color("rgb(1, 2, 255)"), "#0102ff")
        self.assertEqual(parse_color("darkgray"), "ansibrightblack")
        self.assertIsNone(parse_color("transparent"))
        self.assertEqual(parse_color("rgb(1,2,999)"), "ansicyan")
        self.assertEqual(parse_color("mystery"), "ansicyan")


class FileTests(unittest.TestCase):
    def test_config_path_rules(self):
        self.assertEqual(config_path({"HERDR_CONFIG_PATH": "/a/b.toml", "XDG_CONFIG_HOME": "/x"}), Path("/a/b.toml"))
        self.assertEqual(config_path({"XDG_CONFIG_HOME": "/x"}), Path("/x/herdr/config.toml"))
        self.assertEqual(config_path({}), Path.home() / ".config/herdr/config.toml")

    def test_load_theme_reads_the_selected_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.toml"
            path.write_text('[theme]\nname = "dracula"\n[theme.custom]\naccent = "#010203"\n')
            theme = load_theme({"HERDR_CONFIG_PATH": str(path)})
            self.assertEqual((theme.name, theme.color("accent")), ("dracula", "#010203"))
            xdg = Path(root) / "herdr"
            xdg.mkdir()
            (xdg / "config.toml").write_text('[theme]\nname = "nord"\n')
            self.assertEqual(load_theme({"XDG_CONFIG_HOME": root}).name, "nord")

    def test_missing_or_broken_files_fall_back(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(load_theme({"HERDR_CONFIG_PATH": f"{root}/none.toml"}).name, "catppuccin")
            broken = Path(root) / "bad.toml"
            broken.write_text("[theme\nname = ")
            self.assertEqual(load_theme({"HERDR_CONFIG_PATH": str(broken)}).name, "catppuccin")
            broken.write_bytes(b"\xff\xfe\x00")
            self.assertEqual(load_theme({"HERDR_CONFIG_PATH": str(broken)}).name, "catppuccin")


if __name__ == "__main__":
    unittest.main()
