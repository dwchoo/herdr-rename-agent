"""Herdr theme palettes and prompt_toolkit styles for the plugin dialogs.

Ported from herdrdev/herdr v0.9.3 (commit
7b116c05bfda646af39d2524c54e70c751f57ee8, Apache-2.0): palettes from
``src/app/state.rs``, theme resolution from ``src/app/mod.rs`` and
``src/config/theme.rs``. See THIRD_PARTY_NOTICES.md and docs/r1-source-findings.md.  Values are prompt_toolkit colors: ``#rrggbb``,
an ``ansi*`` name (the ``terminal`` theme), or ``None`` for the terminal default.

Resolution mirrors Herdr (``src/app/mod.rs``) with these documented fallbacks:

* The config file is read once, read-only (``HERDR_CONFIG_PATH``, else
  ``$XDG_CONFIG_HOME/herdr/config.toml``, else ``~/.config/herdr/config.toml``).
  A missing, unreadable or invalid file gives the default theme ``catppuccin``.
* ``[theme] name`` (aliases accepted); an unknown or non-string name gives
  ``catppuccin``.
* ``auto_switch = true`` depends on the host terminal appearance, which a plugin
  cannot read: the dark variant is assumed (``dark_name`` if set, else the dark
  sibling of ``name``) and ``[theme.custom.dark]`` is applied.
* ``[theme.custom]`` tokens, then legacy ``[ui] accent``, override the palette.
  Unparseable colors become cyan, as in Herdr.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_THEME = "catppuccin"
TOKENS = (
    "accent", "panel_bg", "sidebar_bg", "active_row_bg", "selection_bg", "surface0",
    "surface1", "surface_dim", "overlay0", "overlay1", "text", "subtext0", "mauve",
    "green", "yellow", "red", "blue", "teal", "peach",
)

PALETTES: dict[str, dict[str, str | None]] = {
    "catppuccin": {
        "accent": "#89b4fa",
        "panel_bg": "#181825",
        "sidebar_bg": None,
        "active_row_bg": "#1e1e2e",
        "selection_bg": "#313244",
        "surface0": "#313244",
        "surface1": "#45475a",
        "surface_dim": "#1e1e2e",
        "overlay0": "#6c7086",
        "overlay1": "#7f849c",
        "text": "#cdd6f4",
        "subtext0": "#a6adc8",
        "mauve": "#cba6f7",
        "green": "#a6e3a1",
        "yellow": "#f9e2af",
        "red": "#f38ba8",
        "blue": "#89b4fa",
        "teal": "#94e2d5",
        "peach": "#fab387",
    },
    "catppuccin-latte": {
        "accent": "#1e66f5",
        "panel_bg": "#eff1f5",
        "sidebar_bg": None,
        "active_row_bg": "#e6e9ef",
        "selection_bg": "#bdd0f5",
        "surface0": "#ccd0da",
        "surface1": "#bcc0cc",
        "surface_dim": "#e6e9ef",
        "overlay0": "#9ca0b0",
        "overlay1": "#8c8fa1",
        "text": "#4c4f69",
        "subtext0": "#6c6f85",
        "mauve": "#8839ef",
        "green": "#40a02b",
        "yellow": "#df8e1d",
        "red": "#d20f39",
        "blue": "#1e66f5",
        "teal": "#179299",
        "peach": "#fe640b",
    },
    "terminal": {
        "accent": "ansiblue",
        "panel_bg": None,
        "sidebar_bg": None,
        "active_row_bg": "ansibrightblack",
        "selection_bg": None,
        "surface0": None,
        "surface1": "ansibrightblack",
        "surface_dim": "ansibrightblack",
        "overlay0": "ansigray",
        "overlay1": "ansiwhite",
        "text": None,
        "subtext0": "ansigray",
        "mauve": "ansigray",
        "green": "ansigreen",
        "yellow": "ansiyellow",
        "red": "ansibrightred",
        "blue": "ansiblue",
        "teal": "ansicyan",
        "peach": "ansiyellow",
    },
    "tokyo-night": {
        "accent": "#7aa2f7",
        "panel_bg": "#1a1b26",
        "sidebar_bg": None,
        "active_row_bg": "#232636",
        "selection_bg": "#2d3650",
        "surface0": "#24283b",
        "surface1": "#414868",
        "surface_dim": "#1a1b26",
        "overlay0": "#565f89",
        "overlay1": "#697196",
        "text": "#c0caf5",
        "subtext0": "#a9b1d6",
        "mauve": "#bb9af7",
        "green": "#9ece6a",
        "yellow": "#e0af68",
        "red": "#f7768e",
        "blue": "#7aa2f7",
        "teal": "#7dcfff",
        "peach": "#ff9e64",
    },
    "tokyo-night-day": {
        "accent": "#2e7de9",
        "panel_bg": "#e1e2e7",
        "sidebar_bg": None,
        "active_row_bg": "#d2d3da",
        "selection_bg": "#b6cae7",
        "surface0": "#c4c8da",
        "surface1": "#a8aecb",
        "surface_dim": "#d2d3da",
        "overlay0": "#8990b3",
        "overlay1": "#68709a",
        "text": "#3760bf",
        "subtext0": "#6172b0",
        "mauve": "#7847bd",
        "green": "#587539",
        "yellow": "#8c6c3e",
        "red": "#f52a65",
        "blue": "#2e7de9",
        "teal": "#118c74",
        "peach": "#b15c00",
    },
    "dracula": {
        "accent": "#bd93f9",
        "panel_bg": "#282a36",
        "sidebar_bg": None,
        "active_row_bg": "#373c52",
        "selection_bg": "#463f5d",
        "surface0": "#44475a",
        "surface1": "#6272a4",
        "surface_dim": "#282a36",
        "overlay0": "#6272a4",
        "overlay1": "#828cb4",
        "text": "#f8f8f2",
        "subtext0": "#d2d2dc",
        "mauve": "#ff79c6",
        "green": "#50fa7b",
        "yellow": "#f1fa8c",
        "red": "#ff5555",
        "blue": "#8be9fd",
        "teal": "#8be9fd",
        "peach": "#ffb86c",
    },
    "nord": {
        "accent": "#88c0d0",
        "panel_bg": "#2e3440",
        "sidebar_bg": None,
        "active_row_bg": "#434c5e",
        "selection_bg": "#40505d",
        "surface0": "#3b4252",
        "surface1": "#434c5e",
        "surface_dim": "#2e3440",
        "overlay0": "#4c566a",
        "overlay1": "#646e82",
        "text": "#eceff4",
        "subtext0": "#d8dee9",
        "mauve": "#b48ead",
        "green": "#a3be8c",
        "yellow": "#ebcb8b",
        "red": "#bf616a",
        "blue": "#81a1c1",
        "teal": "#8fbcbb",
        "peach": "#d08770",
    },
    "gruvbox": {
        "accent": "#d79921",
        "panel_bg": "#282828",
        "sidebar_bg": None,
        "active_row_bg": "#323130",
        "selection_bg": "#4b3f27",
        "surface0": "#3c3836",
        "surface1": "#504945",
        "surface_dim": "#282828",
        "overlay0": "#928374",
        "overlay1": "#a89984",
        "text": "#ebdbb2",
        "subtext0": "#d5c4a1",
        "mauve": "#d3869b",
        "green": "#b8bb26",
        "yellow": "#fabd2f",
        "red": "#fb4934",
        "blue": "#83a598",
        "teal": "#8ec07c",
        "peach": "#fe8019",
    },
    "gruvbox-light": {
        "accent": "#076678",
        "panel_bg": "#fbf1c7",
        "sidebar_bg": None,
        "active_row_bg": "#f2e5bc",
        "selection_bg": "#ebdbb2",
        "surface0": "#ebdbb2",
        "surface1": "#d5c4a1",
        "surface_dim": "#f2e5bc",
        "overlay0": "#928374",
        "overlay1": "#7c6f64",
        "text": "#3c3836",
        "subtext0": "#504945",
        "mauve": "#8f3f71",
        "green": "#79740e",
        "yellow": "#b57614",
        "red": "#9d0006",
        "blue": "#076678",
        "teal": "#427b58",
        "peach": "#af3a03",
    },
    "one-dark": {
        "accent": "#61afef",
        "panel_bg": "#282c34",
        "sidebar_bg": None,
        "active_row_bg": "#313640",
        "selection_bg": "#334659",
        "surface0": "#2c313a",
        "surface1": "#3e4451",
        "surface_dim": "#282c34",
        "overlay0": "#5c6370",
        "overlay1": "#737a87",
        "text": "#abb2bf",
        "subtext0": "#969ca8",
        "mauve": "#c678dd",
        "green": "#98c379",
        "yellow": "#e5c07b",
        "red": "#e06c75",
        "blue": "#61afef",
        "teal": "#56b6c2",
        "peach": "#d19a66",
    },
    "one-light": {
        "accent": "#4078f2",
        "panel_bg": "#fafafa",
        "sidebar_bg": None,
        "active_row_bg": "#d8dbe2",
        "selection_bg": "#cddbf8",
        "surface0": "#f0f0f1",
        "surface1": "#e5e5e6",
        "surface_dim": "#f5f5f6",
        "overlay0": "#a0a1a7",
        "overlay1": "#686b77",
        "text": "#383a42",
        "subtext0": "#686b77",
        "mauve": "#a626a4",
        "green": "#50a14f",
        "yellow": "#c18401",
        "red": "#e45649",
        "blue": "#4078f2",
        "teal": "#0184bc",
        "peach": "#986801",
    },
    "solarized": {
        "accent": "#268bd2",
        "panel_bg": "#002b36",
        "sidebar_bg": None,
        "active_row_bg": "#164b57",
        "selection_bg": "#083e55",
        "surface0": "#073642",
        "surface1": "#586e75",
        "surface_dim": "#002b36",
        "overlay0": "#586e75",
        "overlay1": "#657b83",
        "text": "#93a1a1",
        "subtext0": "#839496",
        "mauve": "#d33682",
        "green": "#859900",
        "yellow": "#b58900",
        "red": "#dc322f",
        "blue": "#268bd2",
        "teal": "#2aa198",
        "peach": "#cb4b16",
    },
    "solarized-light": {
        "accent": "#268bd2",
        "panel_bg": "#fdf6e3",
        "sidebar_bg": None,
        "active_row_bg": "#eee8d5",
        "selection_bg": "#c9dcdf",
        "surface0": "#eee8d5",
        "surface1": "#93a1a1",
        "surface_dim": "#eee8d5",
        "overlay0": "#93a1a1",
        "overlay1": "#586e75",
        "text": "#657b83",
        "subtext0": "#839496",
        "mauve": "#d33682",
        "green": "#859900",
        "yellow": "#b58900",
        "red": "#dc322f",
        "blue": "#268bd2",
        "teal": "#2aa198",
        "peach": "#cb4b16",
    },
    "kanagawa": {
        "accent": "#7e9cd8",
        "panel_bg": "#1f1f28",
        "sidebar_bg": None,
        "active_row_bg": "#363646",
        "selection_bg": "#32384b",
        "surface0": "#2a2a37",
        "surface1": "#363646",
        "surface_dim": "#1f1f28",
        "overlay0": "#727169",
        "overlay1": "#87867d",
        "text": "#dcd7ba",
        "subtext0": "#c8c3aa",
        "mauve": "#957fb8",
        "green": "#76946a",
        "yellow": "#c0a36e",
        "red": "#c34043",
        "blue": "#7e9cd8",
        "teal": "#7fb4ca",
        "peach": "#ffa066",
    },
    "kanagawa-lotus": {
        "accent": "#4d699b",
        "panel_bg": "#f2ecbc",
        "sidebar_bg": None,
        "active_row_bg": "#d5cea3",
        "selection_bg": "#dcd5ac",
        "surface0": "#dcd5ac",
        "surface1": "#c9cbd1",
        "surface_dim": "#d5cea3",
        "overlay0": "#a09cac",
        "overlay1": "#8a8980",
        "text": "#545464",
        "subtext0": "#43436c",
        "mauve": "#624c83",
        "green": "#6f894e",
        "yellow": "#77713f",
        "red": "#c84053",
        "blue": "#4d699b",
        "teal": "#4e8ca2",
        "peach": "#cc6d00",
    },
    "rose-pine": {
        "accent": "#c4a7e7",
        "panel_bg": "#191724",
        "sidebar_bg": None,
        "active_row_bg": "#26233a",
        "selection_bg": "#3b344b",
        "surface0": "#1f1d2e",
        "surface1": "#26233a",
        "surface_dim": "#26233a",
        "overlay0": "#6e6a86",
        "overlay1": "#908caa",
        "text": "#e0def4",
        "subtext0": "#c8c5dc",
        "mauve": "#c4a7e7",
        "green": "#31748f",
        "yellow": "#f6c177",
        "red": "#eb6f92",
        "blue": "#31748f",
        "teal": "#9ccfd8",
        "peach": "#ea9a97",
    },
    "rose-pine-dawn": {
        "accent": "#907aa9",
        "panel_bg": "#faf4ed",
        "sidebar_bg": None,
        "active_row_bg": "#e3d9cf",
        "selection_bg": "#f2e9e1",
        "surface0": "#f2e9e1",
        "surface1": "#fffaf3",
        "surface_dim": "#f2e9e1",
        "overlay0": "#9893a5",
        "overlay1": "#797593",
        "text": "#464261",
        "subtext0": "#797593",
        "mauve": "#907aa9",
        "green": "#286983",
        "yellow": "#ea9d34",
        "red": "#b4637a",
        "blue": "#286983",
        "teal": "#56949f",
        "peach": "#d7827e",
    },
    "vesper": {
        "accent": "#ffc799",
        "panel_bg": "#1a1a1a",
        "sidebar_bg": None,
        "active_row_bg": "#101010",
        "selection_bg": "#232323",
        "surface0": "#232323",
        "surface1": "#282828",
        "surface_dim": "#101010",
        "overlay0": "#5c5c5c",
        "overlay1": "#7e7e7e",
        "text": "#ffffff",
        "subtext0": "#a0a0a0",
        "mauve": "#ffd1a8",
        "green": "#99ffe4",
        "yellow": "#ffc799",
        "red": "#ff8080",
        "blue": "#b0b0b0",
        "teal": "#66ddcc",
        "peach": "#ffc799",
    },
}

ALIASES = {
    "catppuccin-mocha": "catppuccin", "latte": "catppuccin-latte",
    "light": "catppuccin-latte", "tokyonight": "tokyo-night",
    "tokyo-day": "tokyo-night-day", "tokyonight-day": "tokyo-night-day",
    "gruvbox-dark": "gruvbox", "onedark": "one-dark", "onelight": "one-light",
    "solarized-dark": "solarized", "lotus": "kanagawa-lotus", "rosepine": "rose-pine",
    "rosepine-dawn": "rose-pine-dawn", "dawn": "rose-pine-dawn",
}
DARK_SIBLINGS = {
    "catppuccin-latte": "catppuccin", "tokyo-night-day": "tokyo-night",
    "gruvbox-light": "gruvbox", "one-light": "one-dark",
    "solarized-light": "solarized", "kanagawa-lotus": "kanagawa",
    "rose-pine-dawn": "rose-pine",
}
_NAMED = {
    "black": "ansiblack", "red": "ansired", "green": "ansigreen", "yellow": "ansiyellow",
    "blue": "ansiblue", "magenta": "ansimagenta", "purple": "ansimagenta",
    "cyan": "ansicyan", "white": "ansiwhite", "gray": "ansigray", "grey": "ansigray",
    "darkgray": "ansibrightblack", "darkgrey": "ansibrightblack",
    "lightred": "ansibrightred", "lightgreen": "ansibrightgreen",
    "lightyellow": "ansibrightyellow", "lightblue": "ansibrightblue",
    "lightmagenta": "ansibrightmagenta", "lightcyan": "ansibrightcyan",
}
_RESET = {"reset", "default", "none", "transparent"}


def canonical_name(name: str) -> str:
    key = name.strip().lower().replace(" ", "-").replace("_", "-")
    return ALIASES.get(key, key)


def parse_color(value: str) -> str | None:
    """Herdr color string to a prompt_toolkit color; unknown strings are cyan."""
    text = value.strip().lower()
    if text in _RESET:
        return None
    if re.fullmatch(r"#[0-9a-f]{6}", text):
        return text
    if re.fullmatch(r"#[0-9a-f]{3}", text):
        return "#" + "".join(char * 2 for char in text[1:])
    match = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", text)
    if match and all(int(part) <= 255 for part in match.groups()):
        return "#%02x%02x%02x" % tuple(int(part) for part in match.groups())
    return _NAMED.get(text, "ansicyan")


def config_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    if env.get("HERDR_CONFIG_PATH"):
        return Path(env["HERDR_CONFIG_PATH"]).expanduser()
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "herdr" / "config.toml"


def _known(name: Any) -> str | None:
    if isinstance(name, str) and canonical_name(name) in PALETTES:
        return canonical_name(name)
    return None


def _apply(palette: dict[str, str | None], overrides: Any) -> None:
    if isinstance(overrides, dict):
        for token in TOKENS:
            if isinstance(overrides.get(token), str):
                palette[token] = parse_color(overrides[token])


@dataclass(frozen=True)
class Theme:
    name: str
    palette: Mapping[str, str | None]

    def color(self, token: str) -> str | None:
        return self.palette[token]

    @property
    def contrast(self) -> str | None:
        """Text color on the accent: the panel color, or surface_dim if that is Reset."""
        return self.palette["panel_bg"] or self.palette["surface_dim"]

    def spec(self, fg: str | None = None, bg: str | None = None, bold: bool = False) -> str:
        parts = []
        if fg:
            parts.append("fg:" + fg)
        if bg:
            parts.append("bg:" + bg)
        if bold:
            parts.append("bold")
        return " ".join(parts)

    def styles(self) -> dict[str, str]:
        """Class name to prompt_toolkit style, as the native rename overlay uses them."""
        p, spec = self.palette, self.spec
        return {
            "panel": spec(p["text"], p["panel_bg"]),
            "title": spec(p["text"], p["panel_bg"], True),
            "input": spec(p["text"], p["surface0"]),
            "primary": spec(self.contrast, p["accent"], True),
            "secondary": spec(p["text"], p["surface0"], True),
            "disabled": spec(p["overlay0"], p["surface0"], True),
            "error": spec(p["red"], p["panel_bg"]),
            "hint": spec(p["subtext0"], p["panel_bg"]),
        }


def theme_from_config(config: Mapping[str, Any] | None) -> Theme:
    config = config or {}
    section = config.get("theme")
    section = section if isinstance(section, dict) else {}
    manual = _known(section.get("name")) or DEFAULT_THEME
    name = manual
    auto = section.get("auto_switch") is True
    if auto:
        name = _known(section.get("dark_name")) or DARK_SIBLINGS.get(manual, manual)
    palette = dict(PALETTES[name])
    custom = section.get("custom")
    _apply(palette, custom)
    ui = config.get("ui")
    accent = ui.get("accent") if isinstance(ui, dict) else None
    if (
        isinstance(accent, str)
        and accent.strip().lower() != "cyan"
        and not (isinstance(custom, dict) and isinstance(custom.get("accent"), str))
    ):
        palette["accent"] = parse_color(accent)
    if auto and isinstance(custom, dict):
        _apply(palette, custom.get("dark"))
    return Theme(name, palette)


def load_theme(environ: Mapping[str, str] | None = None) -> Theme:
    """Resolve the active theme; never raises and never touches the Herdr API."""
    try:
        with config_path(environ).open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, ValueError, UnicodeError):
        config = {}
    return theme_from_config(config)
