"""Interactive menu layer: input modes, alias matching, the dmenu/bemenu/fuzzel
runner with its icon support, and the generic prompt/choice helpers.

This module knows nothing about ingestions; the application registers the
alias tables that need special display (see register_alias_table) and fills
MENU_ICONS with the icon names for its choices.
"""

import functools
import os
import re
import shlex
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from typing import Callable

import typer
from rich.console import Console

con = Console()
err_con = Console(stderr=True)
app_dir = Path(typer.get_app_dir("logpy"))

# Icon names (freedesktop) attached to menu choices in fuzzel mode. The
# application fills this with its own choice labels.
MENU_ICONS: dict[str, str] = {}

# Alias tables with special display rules, keyed by id(table): a canonicaliser
# applied to candidates (e.g. route formatting) and whether labels are shown
# capitalised.
_CANONICALIZERS: dict[int, Callable[[str], str | None]] = {}
_CAPITALIZED_TABLES: set[int] = set()


def register_alias_table(aliases: dict[str, list[str]], canonicalize: Callable[[str], str | None] | None = None, capitalize: bool = False) -> None:
    if canonicalize is not None:
        _CANONICALIZERS[id(aliases)] = canonicalize
    if capitalize:
        _CAPITALIZED_TABLES.add(id(aliases))


def _canonicalize_for(aliases: dict[str, list[str]] | None, candidate: str) -> str | None:
    canonicalize = _CANONICALIZERS.get(id(aliases)) if aliases is not None else None
    return canonicalize(candidate) if canonicalize else None


def _safe_path_part(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


BACK_CHOICE = "Back"

MENU_CUSTOM_PREFIX = "~"

MENU_WIDTH_FACTOR = "0.75"

MENU_MOUSE_ENABLED = True

MENU_TOOL_COMMAND: list[str] = []

MENU_PROMPT_PLACEHOLDER = "{prompt}"

FUZZEL_MENU_TOOL = "fuzzel -d -f Monospace-8 --use-bold -w 90 -l 25 -x 5 -y 5 --no-exit-on-keyboard-focus-loss --keyboard-focus=exclusive --no-mouse -b 101418ff -t a799cdff --prompt-color=ffffffff --input-color=ff00ffff --selection-color=080a0cff --selection-text-color=a99acfff --selection-match-color=ff00ffff --border-color=a2a9b1ff --border-width=1 --border-radius=0"

MENU_ICON_COLOR = "#ffffff"

MENU_ICON_CACHE_DIR = app_dir / "icons"

MENU_ICON_FALLBACK_THEMES = ("Adwaita", "hicolor")

ICON_COLOR_RE = re.compile(r'((?:fill|stroke|stop-color|color)\s*[:=]\s*["\']?)(#[0-9a-fA-F]{3,8}|currentColor)\b')

EXTRA_INFO_ALIASES = {
    "No": ["n"],
    "Yes": ["y"],
}

class BackRequested(Exception):
    pass

class InputMode(str, Enum):
    flags = "flags"
    prompt = "prompt"
    dmenu = "dmenu"
    bemenu = "bemenu"
    menu = "menu"

@functools.lru_cache(maxsize=200_000)
def _alias_key(value: str) -> str:
    stripped = value.strip()
    key = "".join(char for char in stripped.casefold() if char.isalnum())
    return key or stripped

# Alias lookup tables are rebuilt only when their alias dict changes. Entries
# keep a reference to the dict so its id cannot be reused while cached.
_ALIAS_LOOKUP_CACHE: dict[int, tuple[dict[str, list[str]], dict[str, str]]] = {}

def _invalidate_alias_lookups() -> None:
    _ALIAS_LOOKUP_CACHE.clear()

def _alias_lookup(aliases: dict[str, list[str]]) -> dict[str, str]:
    if not aliases:
        return {}
    cached = _ALIAS_LOOKUP_CACHE.get(id(aliases))
    if cached is not None and cached[0] is aliases:
        return cached[1]
    lookup = {}
    for canonical, variants in aliases.items():
        lookup[_alias_key(canonical)] = canonical
        for variant in variants:
            lookup[_alias_key(variant)] = canonical
    if len(_ALIAS_LOOKUP_CACHE) > 64:
        _ALIAS_LOOKUP_CACHE.clear()
    _ALIAS_LOOKUP_CACHE[id(aliases)] = (aliases, lookup)
    return lookup

def _resolve_alias(value: str | None, aliases: dict[str, list[str]]) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return stripped
    return _alias_lookup(aliases).get(_alias_key(stripped), stripped)

def _choice_label(canonical: str, aliases: dict[str, list[str]]) -> str:
    variants = aliases.get(canonical)
    display = canonical.capitalize() if id(aliases) in _CAPITALIZED_TABLES else canonical
    if not variants:
        return display
    return f"{display} ({', '.join(variants)})"

def _strip_base_menu_choice_marker(choice: str) -> str:
    choice = choice.strip()
    if choice.startswith(MENU_CUSTOM_PREFIX):
        return choice.removeprefix(MENU_CUSTOM_PREFIX).lstrip()
    return choice

def _menu_candidates(
    candidates: list[str],
    aliases: dict[str, list[str]] | None = None,
    labels_by_value: dict[str, str] | None = None,
    keep_custom_prefix: bool = False,
) -> tuple[list[str], dict[str, str]]:
    aliases = aliases or {}
    labels_by_value = labels_by_value or {}
    labels = []
    label_values = {}
    seen = set()

    for candidate in candidates:
        if not candidate:
            continue
        candidate = candidate.strip() if keep_custom_prefix else _strip_base_menu_choice_marker(candidate)
        if not candidate:
            continue
        if _alias_key(candidate) == _alias_key(BACK_CHOICE):
            # The back choice is navigation, not a value: keep its label as is.
            canonical = BACK_CHOICE
        else:
            canonical = _canonicalize_for(aliases, candidate)
            canonical = canonical or _resolve_alias(candidate, aliases) or candidate
        key = _alias_key(canonical)
        if key in seen:
            continue
        seen.add(key)
        label = labels_by_value.get(canonical) or _choice_label(canonical, aliases)
        labels.append(label)
        label_values[label] = canonical

    return labels, label_values

def _prompt_for_value(label: str, current: str | None = None, optional: bool = False) -> str | None:
    value = typer.prompt(label, default=current or "", show_default=bool(current))
    value = value.strip()
    if value:
        return value
    if optional:
        return None
    raise typer.BadParameter(f"{label} is required")

def _parse_menu_tool(value: str | None) -> list[str]:
    if not value or not value.strip():
        return []
    try:
        return shlex.split(value)
    except ValueError as exc:
        raise typer.BadParameter(f"Could not parse --menu-tool command: {exc}") from exc

def _menu_tool_is_fuzzel() -> bool:
    return bool(MENU_TOOL_COMMAND) and Path(MENU_TOOL_COMMAND[0]).name == "fuzzel"

def _menu_tool_command(label: str) -> list[str]:
    """Build the custom menu command. "{prompt}" in any argument is replaced by
    the prompt text; without it, "-p <prompt>" is appended as dmenu expects."""
    if _menu_tool_is_fuzzel():
        label = f"{label}: "
    if any(MENU_PROMPT_PLACEHOLDER in part for part in MENU_TOOL_COMMAND):
        return [part.replace(MENU_PROMPT_PLACEHOLDER, label) for part in MENU_TOOL_COMMAND]
    return [*MENU_TOOL_COMMAND, "-p", label]

def _menu_icons_enabled() -> bool:
    return _menu_tool_is_fuzzel()

def _fuzzel_icon_theme() -> str:
    """The icon theme fuzzel will use: --icon-theme on its command line, else the
    icon-theme key of its fuzzel.ini, else fuzzel's default (hicolor)."""
    args = MENU_TOOL_COMMAND
    config_path = None
    for index, arg in enumerate(args):
        if arg.startswith("--icon-theme="):
            return arg.split("=", 1)[1]
        if arg in ("--icon-theme", "-I") and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--config="):
            config_path = arg.split("=", 1)[1]
        if arg in ("--config", "-c") and index + 1 < len(args):
            config_path = args[index + 1]
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    config_file = Path(config_path).expanduser() if config_path else config_home / "fuzzel" / "fuzzel.ini"
    try:
        text = config_file.read_text()
    except OSError:
        return "hicolor"
    section = "main"
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section == "main" and "=" in line:
            key, value = (part.strip() for part in line.split("=", 1))
            if key == "icon-theme" and value:
                return value
    return "hicolor"

def _icon_base_dirs() -> list[Path]:
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    candidates = [
        Path.home() / ".icons",
        data_home / "icons",
        *[Path(directory) / "icons" for directory in data_dirs.split(":") if directory],
        Path("/run/current-system/sw/share/icons"),
    ]
    unique = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique

def _theme_inherits(theme_dirs: list[Path]) -> list[str]:
    for theme_dir in theme_dirs:
        try:
            text = (theme_dir / "index.theme").read_text()
        except OSError:
            continue
        for line in text.splitlines():
            if line.strip().startswith("Inherits="):
                return [name.strip() for name in line.split("=", 1)[1].split(",") if name.strip()]
    return []

_THEME_ICON_INDEX: dict[Path, dict[str, Path]] = {}

def _theme_dir_icons(theme_dir: Path) -> dict[str, Path]:
    index = _THEME_ICON_INDEX.get(theme_dir)
    if index is None:
        index = {}
        for path in sorted(theme_dir.rglob("*.svg")):
            parts = path.relative_to(theme_dir).parts
            rank = (0 if "symbolic" in parts else 1, 0 if "scalable" in parts else 1, len(parts))
            current = index.get(path.stem)
            if current is None or rank < current[0]:
                index[path.stem] = (rank, path)
        index = {stem: path for stem, (_, path) in index.items()}
        _THEME_ICON_INDEX[theme_dir] = index
    return index

def _find_theme_icon(theme: str, icon_name: str) -> Path | None:
    base_dirs = _icon_base_dirs()
    queue = [theme]
    visited = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        theme_dirs = [base / current for base in base_dirs if (base / current).is_dir()]
        for theme_dir in theme_dirs:
            found = _theme_dir_icons(theme_dir).get(icon_name)
            if found:
                return found
        queue.extend(_theme_inherits(theme_dirs))
        if not queue:
            queue.extend(name for name in MENU_ICON_FALLBACK_THEMES if name not in visited)
    return None

def _recolored_icon(icon_name: str) -> str:
    """Return a path to a copy of the theme's icon filled with MENU_ICON_COLOR,
    or the bare icon name when no theme file is found."""
    if not MENU_ICON_COLOR:
        return icon_name
    theme = _fuzzel_icon_theme()
    cache_file = MENU_ICON_CACHE_DIR / _safe_path_part(MENU_ICON_COLOR) / _safe_path_part(theme) / f"{icon_name}.svg"
    if cache_file.is_file():
        return str(cache_file)
    source = _find_theme_icon(theme, icon_name)
    if source is None:
        return icon_name
    try:
        text = source.read_text()
    except OSError:
        return icon_name
    recolored = ICON_COLOR_RE.sub(lambda match: f"{match.group(1)}{MENU_ICON_COLOR}", text)
    if recolored == text:
        recolored = re.sub(r"<svg\b", f'<svg fill="{MENU_ICON_COLOR}"', text, count=1)
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(recolored)
    except OSError:
        return icon_name
    return str(cache_file)

def _menu_entry_with_icon(label: str, value: str | None) -> str:
    icon_name = MENU_ICONS.get(value or "") or MENU_ICONS.get(label)
    if not icon_name:
        return label
    return f"{label}\0icon\x1f{_recolored_icon(icon_name)}"

def _menu_for_value(
    executable: str,
    label: str,
    current: str | None = None,
    candidates: list[str] | None = None,
    optional: bool = False,
    aliases: dict[str, list[str]] | None = None,
    labels_by_value: dict[str, str] | None = None,
    allow_back: bool = False,
    keep_custom_prefix: bool = False,
) -> str | None:
    if executable == InputMode.menu.value:
        if not MENU_TOOL_COMMAND:
            raise typer.BadParameter("--menu-tool must name the menu command")
        executable_name = MENU_TOOL_COMMAND[0]
    else:
        executable_name = executable
    if not shutil.which(executable_name):
        raise typer.BadParameter(f"{executable_name} was not found in PATH")

    menu_candidates = candidates or ([current] if current else [])
    if allow_back:
        menu_candidates = [*menu_candidates, BACK_CHOICE]
    labels, label_values = _menu_candidates(menu_candidates, aliases, labels_by_value, keep_custom_prefix)
    if executable == InputMode.menu.value and _menu_icons_enabled():
        menu_lines = [_menu_entry_with_icon(item, label_values.get(item)) for item in labels]
    else:
        menu_lines = labels
    menu_input = "\n".join(menu_lines)
    if menu_input:
        menu_input += "\n"
    if executable == InputMode.menu.value:
        command = _menu_tool_command(label)
    else:
        command = [executable, "-p", label]
    if executable in (InputMode.dmenu.value, InputMode.bemenu.value):
        command.extend(["-i", "-W", MENU_WIDTH_FACTOR])
        if not MENU_MOUSE_ENABLED:
            command.append("--no-cursor")
            command.append("--single-instance")
    try:
        completed = subprocess.run(
            command,
            input=menu_input,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise typer.BadParameter(f"Could not run {executable}: {exc}") from exc

    if completed.returncode == 1:
        raise typer.Exit(0)
    if completed.returncode != 0:
        raise typer.BadParameter(completed.stderr.strip() or f"{executable} failed")

    value = completed.stdout.strip()
    if allow_back and _alias_key(value) == _alias_key(BACK_CHOICE):
        raise BackRequested
    if value:
        selected = label_values.get(value)
        if selected is None and not keep_custom_prefix and value.startswith(MENU_CUSTOM_PREFIX):
            selected = value.removeprefix(MENU_CUSTOM_PREFIX).strip()
        return selected or _resolve_alias(value, aliases or {})
    if optional:
        return None
    raise typer.BadParameter(f"{label} is required")

def _prompt_mode_choice(label: str, current: str, aliases: dict[str, list[str]]) -> str:
    value = _prompt_for_value(label, current, optional=False)
    return _resolve_alias(value, aliases) or value

def _print_prompt_choices(label: str, choices: list[str]) -> None:
    con.print(f"{label}:")
    for index, choice in enumerate(choices, 1):
        con.print(f"{index}. {choice}")

def _interactive_choice(
    mode: InputMode,
    label: str,
    current: str,
    choices: list[str],
    aliases: dict[str, list[str]] | None,
) -> str:
    if mode == InputMode.prompt:
        _print_prompt_choices(label, choices)
        value = _prompt_for_value(label, current, optional=False)
        try:
            selected_index = int(value)
        except (TypeError, ValueError):
            return _resolve_alias(value, aliases or {}) or value
        if selected_index < 1 or selected_index > len(choices):
            raise typer.BadParameter(f"{label} selection out of range")
        return choices[selected_index - 1]
    return _menu_for_value(
        mode.value,
        label,
        current,
        candidates=choices,
        aliases=aliases,
        labels_by_value={choice: choice for choice in choices},
    ) or current

def _select_statistics_choice(
    mode: InputMode,
    label: str,
    choices: list[str],
    labels_by_value: dict[str, str] | None = None,
    back_choice: str = BACK_CHOICE,
) -> str:
    choices = [*choices, back_choice]
    labels_by_value = {**(labels_by_value or {}), back_choice: back_choice}
    if mode == InputMode.prompt:
        labels = [labels_by_value.get(choice, choice) for choice in choices]
        _print_prompt_choices(label, labels)
        selected = _prompt_for_value(label, labels[0], optional=False)
        try:
            selected_index = int(selected)
        except (TypeError, ValueError):
            for choice, choice_label in zip(choices, labels):
                if selected == choice or selected == choice_label:
                    return choice
            return selected
        if selected_index < 1 or selected_index > len(choices):
            raise typer.BadParameter(f"{label} selection out of range")
        return choices[selected_index - 1]
    selected = _menu_for_value(
        mode.value,
        label,
        choices[0],
        candidates=choices,
        labels_by_value=labels_by_value,
    )
    return selected or back_choice

def _prompt_free_text(mode: InputMode, label: str) -> str | None:
    """Free text; in menu modes only Back is listed so typed text comes back verbatim."""
    if mode == InputMode.prompt:
        return _prompt_for_value(label, None, optional=True)
    text = _menu_for_value(mode.value, label, None, candidates=[BACK_CHOICE], optional=True)
    if text is None or _alias_key(text) == _alias_key(BACK_CHOICE):
        return None
    return text

def _confirm_choice(mode: InputMode, question: str) -> bool:
    if mode == InputMode.prompt:
        return typer.confirm(question, default=False)
    answer = _menu_for_value(mode.value, question, "No", candidates=["No", "Yes"], aliases=EXTRA_INFO_ALIASES, optional=True)
    return answer == "Yes"
