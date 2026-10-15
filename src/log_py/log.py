import csv
import ast
import dateparser
import io
import functools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
import requests
from pydantic import BaseModel, HttpUrl, field_validator
from typer_config import toml_loader, conf_callback_factory
from rich.console import Console

from log_py.salts import SALT_ALIASES, SALT_CHOICES

con = Console()
err_con = Console(stderr=True)
app = typer.Typer(add_completion=True, no_args_is_help=True)
app_dir = Path(typer.get_app_dir("logpy"))
SUBSTANCE_CACHE_FILE = app_dir / "substances.json"
PROMPT_OPTIONS_CACHE_FILE = app_dir / "prompt-options.json"
PROMPT_OPTIONS_CACHE_VERSION = 2
SUPPLY_CACHE_FILE = app_dir / "supplies.json"
SUPPLY_CACHE_VERSION = 1
STASH_CACHE_FILE = app_dir / "stash.json"
STASH_CACHE_VERSION = 1
WEBHOOK_MESSAGE_CACHE_FILE = app_dir / "webhook-messages.json"
USERPAGE_CACHE_FILE = app_dir / "userpage.json"
ANODYNE_USERPAGE_RE = re.compile(r"^(?P<base>https?://anodyne\.wiki)/user/(?P<name>[^/?#]+)/?(?:[?#].*)?$")
WEBHOOK_MESSAGE_CACHE_VERSION = 1
SKIP_CHOICE = "Skip"
LEGACY_UNKNOWN_CHOICE = "Unknown"
UNKNOWN_SUBSTANCE_TITLE = "Unknown"
DONE_SUBSTANCE_CHOICE = ">"
UNKNOWN_SUBSTANCE_CHOICE = LEGACY_UNKNOWN_CHOICE
UNKNOWN_SITE_CHOICE = SKIP_CHOICE
UNKNOWN_VOLUME_CHOICE = SKIP_CHOICE
UNKNOWN_GAUGE_CHOICE = SKIP_CHOICE
UNKNOWN_LENGTH_CHOICE = SKIP_CHOICE
EMPTY_NOTE_CHOICE = "No note"
NOW_TIME_CHOICE = "now"
BACK_CHOICE = "Back"
MENU_CUSTOM_PREFIX = "~"
ACTION_LOG_INGESTION = "Add ingestion"
ACTION_EDIT_SUPPLY_STACK = "Supplies"
ACTION_EDIT_STASH = "Stash"
ACTION_SETTINGS = "Settings"
ACTION_STATISTICS = "Statistics"
ACTION_HISTORY = "History"
ACTION_USERPAGE = "Userpage"
USERPAGE_ADD_KEY_CHOICE = "Add key"
USERPAGE_ADD_ITEM_CHOICE = "Add item"
USERPAGE_REMOVE_CHOICE = "Remove"
USERPAGE_CLOSE_CHOICE = "Close"
USERPAGE_RESET_CHOICE = "Reset userpage to upstream"
USERPAGE_ANNOTATE_CHOICE = "Add annotations"
USERPAGE_MANUAL_CHOICE = "Manually edit userpage"
JSON_NAME_KEY = "Name"
JSON_MISSING_PREFIX = "missing:"
USERPAGE_SUBSTANCES_KEY = "TriedSubstances"
USERPAGE_ROUTES_KEY = "Routes"
USERPAGE_ROUTE_FIELDS = ["Name", "Dosage", "Onset", "Comeup", "Duration"]
USERPAGE_SUBSTANCE_LIST_FIELDS = [
    "Classes", "Salts", "Sources", "Indication", "Tolerance", "Effects", "After", "Odor", "Notes",
]
JSON_ENTRY_PREFIX = "entry:"
HISTORY_PAGE_SIZE = 100
HISTORY_NEXT_CHOICE = f"Next {HISTORY_PAGE_SIZE}"
HISTORY_GOTO_CHOICE = "Goto Page: ?"
HISTORY_EDIT_CHOICE = "Edit"
HISTORY_REMOVE_CHOICE = "Remove"
LOG_COLUMNS = ["timestamp", "user", "substance", "dosage", "roa", "site", "salt", "note"]
SUPPLY_ACTION_ADD = "Add"
SUPPLY_ACTION_REMOVE = "Remove"
SUPPLY_ACTION_ADD_NEW = "Add new"
SUPPLY_ACTION_ADD_SYRINGE = "Add syringe"
SUPPLY_ACTION_ADD_NEEDLE = "Add needle"
SUPPLY_ACTION_SPECIFY_NEW_SYRINGE = "Specify a new syringe type"
SUPPLY_ACTION_SPECIFY_NEW_NEEDLE = "Specify a new needle type"
SUPPLY_ACTION_REUSE_SYRINGE = "Reuse syringe"
SUPPLY_ACTION_REUSE_NEEDLE = "Reuse needle"
SUPPLY_ACTION_SKIP = SKIP_CHOICE
SUPPLY_ACTION_BACK = BACK_CHOICE
NON_DISPOSABLE_SYRINGE_TOKEN = "non-disposable"
# Populated only from AnodyneWiki API responses cached in SUBSTANCE_CACHE_FILE.
SUBSTANCE_ALIASES: dict[str, list[str]] = {}
ROA_ALIASES = {
    "oral": ["po", "by mouth", "ingested"],
    "sublingual": ["sl", "under tongue"],
    "buccal": ["bucc"],
    "intranasal": ["in", "nasal", "snorted", "insufflated"],
    "inhaled": ["smoked", "vaped", "vaporized"],
    "intravenous": ["iv"],
    "intramuscular": ["im"],
    "subcutaneous": ["sc", "sq", "subq"],
    "intradermal": ["id"],
    "intrarectal": ["ir", "rectal", "plugged", "boofed"],
    "transdermal": ["td", "topical"],
}
UNIT_CHOICES = ["mg", "ug", "mcg", "g", "ml"]
EXTRA_INFO_ALIASES = {
    "No": ["n"],
    "Yes": ["y"],
}
EXTRA_INFO_SKIP_CHOICE = "Skip"
EXTRA_INFO_SHOW_CHOICE = "Show"
EXTRA_INFO_CHOICE_ALIASES = {
    EXTRA_INFO_SKIP_CHOICE: [],
    EXTRA_INFO_SHOW_CHOICE: [],
}
ACTION_ALIASES = {
    ACTION_LOG_INGESTION: ["log", "ingestion", "li"],
    ACTION_EDIT_SUPPLY_STACK: ["supply", "stack", "edit supply", "ess"],
    ACTION_EDIT_STASH: ["stash", "substance stash", "edit stash"],
    ACTION_SETTINGS: ["settings", "config", "preferences"],
    ACTION_STATISTICS: ["statistics", "stats"],
    ACTION_HISTORY: ["history", "logs"],
    ACTION_USERPAGE: ["userpage", "profile"],
}

# Icon names attached to menu choices in fuzzel mode. They are freedesktop icon
# names, resolved by fuzzel through its configured icon theme.
MENU_ICONS = {
    ACTION_LOG_INGESTION: "list-add-symbolic",
    ACTION_EDIT_SUPPLY_STACK: "applications-science-symbolic",
    ACTION_EDIT_STASH: "package-x-generic-symbolic",
    ACTION_HISTORY: "document-open-recent-symbolic",
    ACTION_USERPAGE: "avatar-default-symbolic",
    USERPAGE_ADD_KEY_CHOICE: "list-add-symbolic",
    USERPAGE_ADD_ITEM_CHOICE: "list-add-symbolic",
    USERPAGE_CLOSE_CHOICE: "window-close-symbolic",
    USERPAGE_RESET_CHOICE: "view-refresh-symbolic",
    USERPAGE_ANNOTATE_CHOICE: "list-add-symbolic",
    USERPAGE_MANUAL_CHOICE: "document-edit-symbolic",
    ACTION_STATISTICS: "x-office-spreadsheet-symbolic",
    ACTION_SETTINGS: "emblem-system-symbolic",
    BACK_CHOICE: "go-previous-symbolic",
    DONE_SUBSTANCE_CHOICE: "go-next-symbolic",
    SKIP_CHOICE: "go-jump-symbolic",
    LEGACY_UNKNOWN_CHOICE: "dialog-question-symbolic",
    HISTORY_NEXT_CHOICE: "go-down-symbolic",
    HISTORY_GOTO_CHOICE: "go-jump-symbolic",
    HISTORY_EDIT_CHOICE: "document-edit-symbolic",
    HISTORY_REMOVE_CHOICE: "edit-delete-symbolic",
    SUPPLY_ACTION_ADD: "list-add-symbolic",
    SUPPLY_ACTION_REMOVE: "list-remove-symbolic",
    SUPPLY_ACTION_ADD_NEW: "document-new-symbolic",
    SUPPLY_ACTION_ADD_SYRINGE: "list-add-symbolic",
    SUPPLY_ACTION_ADD_NEEDLE: "list-add-symbolic",
    SUPPLY_ACTION_SPECIFY_NEW_SYRINGE: "document-new-symbolic",
    SUPPLY_ACTION_SPECIFY_NEW_NEEDLE: "document-new-symbolic",
    SUPPLY_ACTION_REUSE_SYRINGE: "view-refresh-symbolic",
    SUPPLY_ACTION_REUSE_NEEDLE: "view-refresh-symbolic",
    EXTRA_INFO_SKIP_CHOICE: "go-jump-symbolic",
    EXTRA_INFO_SHOW_CHOICE: "view-more-symbolic",
    "Yes": "emblem-ok-symbolic",
    "No": "window-close-symbolic",
    EMPTY_NOTE_CHOICE: "edit-clear-symbolic",
    NOW_TIME_CHOICE: "alarm-symbolic",
}
STATISTICS_TIMESCALE_DAILY = "Daily"
STATISTICS_TIMESCALE_WEEKLY = "Weekly"
STATISTICS_TIMESCALE_MONTHLY = "Monthly"
STATISTICS_TIMESCALE_TOTAL = "Total"
STATISTICS_SECTION_SUBSTANCES = "Substances"
STATISTICS_SECTION_ROUTES = "Routes of Administration"
SUPPLY_ACTION_ALIASES = {
    SUPPLY_ACTION_ADD: ["add", "a"],
    SUPPLY_ACTION_REMOVE: ["remove", "r"],
    SUPPLY_ACTION_ADD_NEW: ["add new", "new", "n"],
    SUPPLY_ACTION_ADD_SYRINGE: ["add syringe", "add syringes", "syringe", "as"],
    SUPPLY_ACTION_ADD_NEEDLE: ["add needle", "add needles", "needle", "an"],
    SUPPLY_ACTION_SPECIFY_NEW_SYRINGE: ["new syringe", "specify syringe"],
    SUPPLY_ACTION_SPECIFY_NEW_NEEDLE: ["new needle", "specify needle"],
    SUPPLY_ACTION_REUSE_SYRINGE: ["reuse syringe", "reused syringe", "rs"],
    SUPPLY_ACTION_REUSE_NEEDLE: ["reuse needle", "reused needle", "rn"],
    SUPPLY_ACTION_SKIP: [],
}
EXTRA_INFO_PROMPT = "Extra options? (note, time, dosage by volume)"


def _time_offset_label(minutes: int) -> str:
    if minutes < 60 or minutes % 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


TIME_CHOICES = [
    _time_offset_label(minutes)
    for minutes in range(5, 2 * 60 + 1, 5)
    #for minutes in range(15, 24 * 60 + 1, 15)
]
IV_SITE_BASES = [
    "median-cubital",
    "basilic",
    "cephalic",
    "dorsal-metacarpal",
      "dorsal-venous-network",
      "dorsal-venous-network-thumb",
      "venous-arch",
      "venous-arch-index",
      "venous-arch-middle",
      "venous-arch-ring",
      "venous-arch-little",
    "femoral",
    "jugular",
    "small-saphenous",
    "great-saphenous",
]
IV_SITE_CHOICES = [
    choice
    for site in IV_SITE_BASES
    for choice in (f"left-{site}", f"right-{site}")
]
IV_SITE_SIDES = ["left", "right"]
IV_SITE_WIKIPEDIA_ARTICLES = {
    "median-cubital": "Median_cubital_vein",
    "cephalic": "Cephalic_vein",
    "basilic": "Basilic_vein",
    "dorsal-metacarpal": "Dorsal_metacarpal_veins",
    "femoral": "Femoral_vein",
    "jugular": "Jugular_vein",
    "small-saphenous": "Small_saphenous_vein",
    "dorsal-venous-arch": "Dorsal_venous_arch_of_the_foot",
    "great-saphenous": "Great_saphenous_vein",
}
MENU_WIDTH_FACTOR = "0.75"
DAILY_USAGE_TODAY = "today"
MENU_MOUSE_ENABLED = True
MENU_TOOL_COMMAND: list[str] = []
MENU_PROMPT_PLACEHOLDER = "{prompt}"
FUZZEL_MENU_TOOL = "fuzzel --dmenu"
MENU_ICON_COLOR = "#ffffff"
MENU_ICON_CACHE_DIR = app_dir / "icons"
MENU_ICON_FALLBACK_THEMES = ("Adwaita", "hicolor")
ICON_COLOR_RE = re.compile(r'((?:fill|stroke|stop-color|color)\s*[:=]\s*["\']?)(#[0-9a-fA-F]{3,8}|currentColor)\b')


class BackRequested(Exception):
    pass


class IngestionCancelled(Exception):
    """Raised when the user backs out of an ingestion started from the main menu."""


def _normalize_optional_daily_usage_arg(argv: list[str]) -> None:
    for index, arg in enumerate(argv[1:], 1):
        if arg != "--daily-usage":
            continue
        if index == len(argv) - 1 or argv[index + 1].startswith("-"):
            argv[index] = f"--daily-usage={DAILY_USAGE_TODAY}"


_normalize_optional_daily_usage_arg(sys.argv)


class InputMode(str, Enum):
    flags = "flags"
    prompt = "prompt"
    dmenu = "dmenu"
    bemenu = "bemenu"
    menu = "menu"


class LogKind(str, Enum):
    single = "single"
    solution = "solution"
    mixture = "mixture"


class LogConfig(BaseModel):
    user: str | None = None
    userpage: str | None = None
    logfile: Path | None = None
    webhook: HttpUrl | None = None
    mouse: bool = True
    notify: bool = False

    @field_validator("logfile", mode="after")
    def valiadte_path(cls, logfile: Path):
        logfile = logfile.expanduser()
        if not logfile.is_absolute():
            return app_dir / logfile
        return logfile


def _unique_aliases(title: str, abbreviations: list[str]) -> list[str]:
    seen = {_alias_key(title)}
    aliases = []
    for abbreviation in abbreviations:
        abbreviation = abbreviation.strip()
        key = _alias_key(abbreviation)
        if not abbreviation or key in seen:
            continue
        seen.add(key)
        aliases.append(abbreviation)
    return aliases


def _cached_abbreviations(value) -> list[str]:
    abbreviations = value.get("Abbreviation") if isinstance(value, dict) else None
    if isinstance(abbreviations, str):
        return [abbreviations]
    if isinstance(abbreviations, list):
        return [item for item in abbreviations if isinstance(item, str)]
    return []


def _load_substance_cache() -> dict[str, dict[str, object]]:
    if not SUBSTANCE_CACHE_FILE.exists():
        return {}
    try:
        with open(SUBSTANCE_CACHE_FILE) as infile:
            raw_cache = json.load(infile)
    except (OSError, json.JSONDecodeError):
        return {}

    cache = raw_cache.get("substances", raw_cache) if isinstance(raw_cache, dict) else {}
    if not isinstance(cache, dict):
        return {}

    valid_cache = {}
    for title, entry in cache.items():
        if not isinstance(title, str) or not isinstance(entry, dict):
            continue
        entry_title = entry.get("Title")
        if not isinstance(entry_title, str):
            entry_title = title
        valid_cache[entry_title] = {
            "Title": entry_title,
            "Abbreviation": _unique_aliases(entry_title, _cached_abbreviations(entry)),
            "synced_at": entry.get("synced_at") if isinstance(entry.get("synced_at"), str) else None,
        }
    return valid_cache


def _write_substance_cache(cache: dict[str, dict[str, object]]) -> None:
    SUBSTANCE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"substances": dict(sorted(cache.items()))}
    tmpfile = SUBSTANCE_CACHE_FILE.with_suffix(f"{SUBSTANCE_CACHE_FILE.suffix}.tmp")
    with open(tmpfile, "w") as outfile:
        json.dump(payload, outfile, indent=2, sort_keys=True)
        outfile.write("\n")
    tmpfile.replace(SUBSTANCE_CACHE_FILE)


def _merge_cached_substance_aliases(cache: dict[str, dict[str, object]] | None = None) -> None:
    for title, entry in (cache or _load_substance_cache()).items():
        existing_aliases = SUBSTANCE_ALIASES.get(title, [])
        SUBSTANCE_ALIASES[title] = _unique_aliases(
            title,
            [*existing_aliases, *_cached_abbreviations(entry)],
        )
    _invalidate_alias_lookups()


def _normalize_api_abbreviations(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _fetch_substance_entry_result(substance: str) -> tuple[dict[str, object] | None, bool]:
    try:
        response = requests.get(f"https://anodyne.wiki/api/substance/{substance}", timeout=5)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None, False

    if not isinstance(data, dict):
        return None, False
    if data.get("NotFound"):
        return None, True
    title = data.get("Title")
    if not isinstance(title, str) or not title:
        title = substance
    return {
        "Title": title,
        "Abbreviation": _unique_aliases(title, _normalize_api_abbreviations(data.get("Abbreviation"))),
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, False


def _anodyne_userpage_api_url(userpage: str | None) -> str | None:
    """Map an AnodyneWiki user page URL to its API URL, else None."""
    if not userpage:
        return None
    match = ANODYNE_USERPAGE_RE.match(userpage.strip())
    if not match:
        return None
    return f"{match.group('base')}/api/user/{match.group('name')}"


def _download_userpage(userpage: str | None) -> bool:
    """Download the AnodyneWiki user API document over userpage.json.
    Returns True when the file was written."""
    api_url = _anodyne_userpage_api_url(userpage)
    if not api_url:
        err_con.print("Userpage is not an anodyne.wiki user page; nothing to download", style="bold yellow")
        return False
    try:
        response = requests.get(api_url, timeout=5)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        err_con.print(f"Userpage download failed: '{exc}'", style="bold yellow")
        return False
    try:
        USERPAGE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmpfile = USERPAGE_CACHE_FILE.with_suffix(f"{USERPAGE_CACHE_FILE.suffix}.tmp")
        with open(tmpfile, "w") as outfile:
            json.dump(data, outfile, indent=2)
            outfile.write("\n")
        tmpfile.replace(USERPAGE_CACHE_FILE)
    except OSError as exc:
        err_con.print(f"Could not write {USERPAGE_CACHE_FILE}: '{exc}'", style="bold yellow")
        return False
    con.print(f"Saved userpage to {USERPAGE_CACHE_FILE}.")
    return True


def _ensure_userpage_cache(userpage: str | None) -> None:
    """Download the AnodyneWiki user API document to userpage.json once."""
    if USERPAGE_CACHE_FILE.exists() or not _anodyne_userpage_api_url(userpage):
        return
    _download_userpage(userpage)


def _fetch_substance_entry(substance: str) -> dict[str, object] | None:
    entry, _ = _fetch_substance_entry_result(substance)
    return entry


def _cache_substance_entry(entry: dict[str, object] | None) -> None:
    if not entry:
        return
    title = entry.get("Title")
    if not isinstance(title, str) or not title:
        return

    cache = _load_substance_cache()
    cache[title] = {
        "Title": title,
        "Abbreviation": _unique_aliases(title, _cached_abbreviations(entry)),
        "synced_at": entry.get("synced_at") if isinstance(entry.get("synced_at"), str) else None,
    }
    _write_substance_cache(cache)
    _merge_cached_substance_aliases({title: cache[title]})

def _logfile_cache_key(logfile: Path) -> str:
    return str(logfile.expanduser().resolve(strict=False))

def _logfile_fingerprint(logfile: Path) -> dict[str, int] | None:
    try:
        stat = logfile.stat()
    except OSError:
        return None
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _load_prompt_options_cache() -> dict[str, object]:
    if not PROMPT_OPTIONS_CACHE_FILE.exists():
        return {}
    try:
        with open(PROMPT_OPTIONS_CACHE_FILE) as infile:
            raw_cache = json.load(infile)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw_cache, dict):
        return {}
    logs = raw_cache.get("logs")
    return logs if isinstance(logs, dict) else {}


def _write_prompt_options_cache(cache: dict[str, object]) -> None:
    PROMPT_OPTIONS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": PROMPT_OPTIONS_CACHE_VERSION,
        "logs": dict(sorted(cache.items())),
    }
    tmpfile = PROMPT_OPTIONS_CACHE_FILE.with_suffix(f"{PROMPT_OPTIONS_CACHE_FILE.suffix}.tmp")
    with open(tmpfile, "w") as outfile:
        json.dump(payload, outfile, indent=2, sort_keys=True)
        outfile.write("\n")
    tmpfile.replace(PROMPT_OPTIONS_CACHE_FILE)


def _valid_prompt_option_substances(value) -> list[str]:
    if not isinstance(value, list):
        return []
    substances = []
    seen = set()
    for item in value:
        if not isinstance(item, str):
            continue
        substance = item.strip()
        if not substance or _is_mixture_value(substance) or substance in seen:
            continue
        seen.add(substance)
        substances.append(substance)
    return substances


def _valid_prompt_option_strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    items = []
    seen = set()
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip()
        key = _alias_key(item)
        if not item or key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items


def _valid_prompt_option_dosages(value) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    dosages = {}
    for substance, substance_dosages in value.items():
        if not isinstance(substance, str):
            continue
        valid_dosages = _valid_prompt_option_strings(substance_dosages)
        if valid_dosages:
            dosages[substance] = valid_dosages
    return dosages


def _load_cached_log_choices(logfile: Path) -> dict[str, object] | None:
    entry = _load_prompt_options_cache().get(_logfile_cache_key(logfile))
    if not isinstance(entry, dict):
        return {"substances": [], "dosages": {}, "salts": []} if not logfile.exists() else None
    if entry.get("fingerprint") != _logfile_fingerprint(logfile):
        return {"substances": [], "dosages": {}, "salts": []} if not logfile.exists() else None
    return {
        "substances": _valid_prompt_option_substances(entry.get("substances")),
        "dosages": _valid_prompt_option_dosages(entry.get("dosages")),
        "salts": _valid_prompt_option_strings(entry.get("salts")),
    }


def _store_log_choices(
    logfile: Path,
    substances: list[str],
    dosages: dict[str, list[str]],
    salts: list[str],
) -> None:
    cache = _load_prompt_options_cache()
    cache[_logfile_cache_key(logfile)] = {
        "fingerprint": _logfile_fingerprint(logfile),
        "substances": substances,
        "dosages": dosages,
        "salts": salts,
    }
    _write_prompt_options_cache(cache)


def _add_logged_choice_options(
    logfile: Path,
    substance: str,
    dosage: str,
    salt: str | None,
) -> None:
    raw_entry = _load_prompt_options_cache().get(_logfile_cache_key(logfile))
    entry = raw_entry if isinstance(raw_entry, dict) else {"substances": [], "dosages": {}, "salts": []}
    substances = _valid_prompt_option_substances(entry.get("substances"))
    dosages = _valid_prompt_option_dosages(entry.get("dosages"))
    salts = _valid_prompt_option_strings(entry.get("salts"))

    updated_substances = substances
    for row_substance, row_dosage in zip(
        _split_mixture_values(substance),
        _split_mixture_values(_dosage_without_volume(dosage)),
    ):
        canonical_substance = _resolve_alias(row_substance, SUBSTANCE_ALIASES) or row_substance
        updated_substances = [
            canonical_substance,
            *[item for item in updated_substances if _alias_key(item) != _alias_key(canonical_substance)],
        ]
        substance_dosages = dosages.get(canonical_substance, [])
        dosage_key = _alias_key(row_dosage)
        dosages[canonical_substance] = [
            row_dosage,
            *[item for item in substance_dosages if _alias_key(item) != dosage_key],
        ]

    for row_salt in _split_mixture_values(salt):
        if _is_known_salt_choice(row_salt):
            continue
        salt_key = _alias_key(row_salt)
        salts = [row_salt, *[item for item in salts if _alias_key(item) != salt_key]]

    _store_log_choices(logfile, updated_substances, dosages, salts)


def _sync_choices_from_history(logfile: Path) -> tuple[list[str], dict[str, list[str]], list[str]]:
    _merge_cached_substance_aliases()
    con.print(f"Scanning {logfile}...")
    substances, _, _ = _sync_choice_cache_from_history(logfile)

    cache = _load_substance_cache()
    changed = False
    pending = [substance for substance in substances if substance not in cache and not _is_list_value(substance)]
    con.print(f"Found {len(substances)} substances; looking up {len(pending)} on AnodyneWiki...")
    for index, substance in enumerate(pending, 1):
        con.print(f"[{index}/{len(pending)}] {substance}")
        entry = _fetch_substance_entry(substance)
        if not entry:
            continue
        title = entry.get("Title")
        if isinstance(title, str):
            cache[title] = entry
            changed = True

    if changed:
        _write_substance_cache(cache)
    _merge_cached_substance_aliases(cache)
    return _sync_choice_cache_from_history(logfile)

TIME_OFFSET_RE = re.compile(
    r"^\s*(?P<amount>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*"
    r"(?P<unit>s|seconds?|m|minutes?|h|hours?|d|days?|w|weeks?|M|months?|y|years?)\s+"
    r"(?P<direction>ago|before)\s*$",
)
TIME_OFFSET_PART_RE = re.compile(
    r"(?P<amount>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*"
    r"(?P<unit>seconds?|minutes?|hours?|days?|weeks?|months?|years?|s|m|h|d|w|M|y)"
)
TIME_OFFSET_DIRECTION_RE = re.compile(r"^(?P<body>.+?)\s+(?P<direction>ago|before)\s*$", re.IGNORECASE)
TIME_OFFSET_SEPARATOR_RE = re.compile(r"^\s*(?:,|and)?\s*$", re.IGNORECASE)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    this_month = datetime(year, month, 1, tzinfo=timezone.utc)
    return (next_month - this_month).days


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, _days_in_month(year, month))
    return value.replace(year=year, month=month, day=day)


def _parse_decimal_amount(value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise typer.BadParameter(f"Invalid numeric amount: {value}") from exc
    if amount < 0:
        raise typer.BadParameter("Offset amount must be non-negative")
    return amount


def _apply_time_offset(base: datetime, amount: Decimal, unit: str) -> datetime:
    unit_key = unit.casefold()
    if unit == "M" or unit_key.startswith("month"):
        if amount != amount.to_integral_value():
            raise typer.BadParameter("Month offsets must be whole numbers")
        return _add_months(base, -int(amount))
    if unit_key.startswith("y"):
        if amount != amount.to_integral_value():
            raise typer.BadParameter("Year offsets must be whole numbers")
        return _add_months(base, -int(amount) * 12)

    seconds_by_unit = {
        "s": Decimal("1"),
        "second": Decimal("1"),
        "seconds": Decimal("1"),
        "m": Decimal("60"),
        "minute": Decimal("60"),
        "minutes": Decimal("60"),
        "h": Decimal("3600"),
        "hour": Decimal("3600"),
        "hours": Decimal("3600"),
        "d": Decimal("86400"),
        "day": Decimal("86400"),
        "days": Decimal("86400"),
        "w": Decimal("604800"),
        "week": Decimal("604800"),
        "weeks": Decimal("604800"),
    }
    seconds = amount * seconds_by_unit[unit_key]
    return base - timedelta(seconds=float(seconds))


def _parse_time_offset_parts(offset_time: str) -> list[tuple[Decimal, str]]:
    match = TIME_OFFSET_DIRECTION_RE.match(offset_time.strip())
    if not match:
        raise typer.BadParameter(
            "Offset time must look like '<number> <unit> [and <number> <unit> ...] <ago|before>'"
        )

    body = match.group("body")
    parts = []
    position = 0
    for part_match in TIME_OFFSET_PART_RE.finditer(body):
        separator = body[position:part_match.start()]
        if not TIME_OFFSET_SEPARATOR_RE.match(separator):
            raise typer.BadParameter(f"Invalid offset time: {offset_time}")
        parts.append((
            _parse_decimal_amount(part_match.group("amount")),
            part_match.group("unit"),
        ))
        position = part_match.end()

    if not parts or not TIME_OFFSET_SEPARATOR_RE.match(body[position:]):
        raise typer.BadParameter(f"Invalid offset time: {offset_time}")
    return parts


def _parse_offset_time(offset_time: str) -> datetime:
    parsed = datetime.now(timezone.utc)
    for amount, unit in _parse_time_offset_parts(offset_time):
        parsed = _apply_time_offset(parsed, amount, unit)
    return parsed


def _parse_set_time(set_time: str | None) -> datetime:
    if set_time is None:
        return datetime.now(timezone.utc)
    parsed: datetime | None = dateparser.parse(
        set_time,
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TO_TIMEZONE": "UTC",
            "PREFER_DATES_FROM": "past",
        },
    )
    if parsed is None:
        raise typer.BadParameter(f"Could not parse '{set_time}' as a date/time")
    return parsed.astimezone(timezone.utc)


def _parse_ingestion_time(set_time: str | None, offset_time: str | None) -> datetime:
    if set_time and offset_time:
        raise typer.BadParameter("--set-time and --offset-time cannot be combined")
    if offset_time:
        return _parse_offset_time(offset_time)
    return _parse_set_time(set_time)


def _menu_default_ingestion_time(mode: InputMode, time: str | None, offset_time: str | None, started_at: datetime) -> datetime | None:
    if mode in (InputMode.dmenu, InputMode.bemenu, InputMode.menu) and not time and not offset_time:
        return started_at
    return None


def _coalesce(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


@functools.lru_cache(maxsize=200_000)
def _alias_key(value: str) -> str:
    stripped = value.strip()
    key = "".join(char for char in stripped.casefold() if char.isalnum())
    return key or stripped


def _is_skip_choice(value: str | None) -> bool:
    if not value:
        return False
    return _alias_key(value) in (_alias_key(SKIP_CHOICE), _alias_key(LEGACY_UNKNOWN_CHOICE))


def _supply_key_part(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


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


def _is_list_value(value: str) -> bool:
    return len(_split_bracketed_list_value(value)) > 1


def _is_mixture_value(value: str) -> bool:
    return len(_split_mixture_values(value)) > 1


def _format_route_for_log(roa: str | None) -> str | None:
    if roa is None:
        return None
    plain = re.sub(r"\s*\([^)]*\)\s*$", "", roa.strip().casefold())
    resolved = _resolve_alias(plain, ROA_ALIASES)
    if not resolved:
        return resolved
    return resolved.strip().casefold()


def _choice_label(canonical: str, aliases: dict[str, list[str]]) -> str:
    variants = aliases.get(canonical)
    display = canonical.capitalize() if aliases is ROA_ALIASES else canonical
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
            canonical = _format_route_for_log(candidate) if aliases is ROA_ALIASES else None
            canonical = canonical or _resolve_alias(candidate, aliases) or candidate
        key = _alias_key(canonical)
        if key in seen:
            continue
        seen.add(key)
        label = labels_by_value.get(canonical) or _choice_label(canonical, aliases)
        labels.append(label)
        label_values[label] = canonical

    return labels, label_values


def _resolve_input_mode(
    flags: bool,
    prompt: bool,
    dmenu: bool,
    bemenu: bool,
    fuzzel: bool,
    menu: bool = False,
) -> InputMode:
    selected = [
        mode
        for enabled, mode in (
            (flags, InputMode.flags),
            (prompt, InputMode.prompt),
            (dmenu, InputMode.dmenu),
            (bemenu, InputMode.bemenu),
            (menu, InputMode.menu),
        )
        if enabled
    ]
    if len(selected) > 1:
        raise typer.BadParameter("Choose only one input mode")
    return selected[0] if selected else InputMode.flags


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
    cache_file = MENU_ICON_CACHE_DIR / _supply_key_part(MENU_ICON_COLOR) / _supply_key_part(theme) / f"{icon_name}.svg"
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


VOLUME_RE = re.compile(r"^\s*(?P<amount>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<unit>[a-zA-Zµμ]*)\s*$")
VOLUME_UNIT_TO_ML = {
    "": Decimal("1"),
    "ml": Decimal("1"),
    "milliliter": Decimal("1"),
    "milliliters": Decimal("1"),
    "millilitre": Decimal("1"),
    "millilitres": Decimal("1"),
    "l": Decimal("1000"),
    "liter": Decimal("1000"),
    "liters": Decimal("1000"),
    "litre": Decimal("1000"),
    "litres": Decimal("1000"),
    "kl": Decimal("1000000"),
    "kiloliter": Decimal("1000000"),
    "kiloliters": Decimal("1000000"),
    "kilolitre": Decimal("1000000"),
    "kilolitres": Decimal("1000000"),
    "cl": Decimal("10"),
    "centiliter": Decimal("10"),
    "centiliters": Decimal("10"),
    "centilitre": Decimal("10"),
    "centilitres": Decimal("10"),
    "dl": Decimal("100"),
    "deciliter": Decimal("100"),
    "deciliters": Decimal("100"),
    "decilitre": Decimal("100"),
    "decilitres": Decimal("100"),
    "ul": Decimal("0.001"),
    "µl": Decimal("0.001"),
    "μl": Decimal("0.001"),
    "microliter": Decimal("0.001"),
    "microliters": Decimal("0.001"),
    "microlitre": Decimal("0.001"),
    "microlitres": Decimal("0.001"),
    "nl": Decimal("0.000001"),
    "nanoliter": Decimal("0.000001"),
    "nanoliters": Decimal("0.000001"),
    "nanolitre": Decimal("0.000001"),
    "nanolitres": Decimal("0.000001"),
}


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _format_stash_amount(amount: object) -> str:
    """Stash amounts as shown in menus and messages: rounded to one decimal."""
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return str(amount)
    return _format_decimal(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _route_supports_solution_volume(roa: str) -> bool:
    normalized = roa.strip().casefold().replace("-", " ").replace("_", " ")
    compact = normalized.replace(" ", "")
    return compact in {
        "subcutaneous",
        "subcutaneously",
        "sc",
        "sq",
        "intramuscular",
        "intramuscularly",
        "im",
        "intradermal",
        "intradermally",
        "id",
        "intrarectal",
        "intrarectally",
        "rectal",
        "ir",
        "intravenous",
        "intravenously",
        "iv",
    }


def _is_injection_route(roa: str) -> bool:
    normalized = roa.strip().casefold().replace("-", " ").replace("_", " ")
    compact = normalized.replace(" ", "")
    return compact in {
        "subcutaneous",
        "subcutaneously",
        "sc",
        "sq",
        "intramuscular",
        "intramuscularly",
        "im",
        "intradermal",
        "intradermally",
        "id",
        "intravenous",
        "intravenously",
        "iv",
    }


def _format_solution_volume(volume: str | None) -> str | None:
    if not volume:
        return None
    volume = volume.strip()
    if not volume or _is_skip_choice(volume):
        return None
    match = VOLUME_RE.match(volume)
    if not match:
        raise typer.BadParameter(f"Could not parse '{volume}' as a volume")
    amount = _parse_decimal_amount(match.group("amount"))
    unit = match.group("unit").casefold()
    multiplier = VOLUME_UNIT_TO_ML.get(unit)
    if multiplier is None:
        raise typer.BadParameter(f"Unsupported volume unit: {match.group('unit')}")
    return f"{_format_decimal(amount * multiplier)}ml"


def _append_solution_volume(dosage: str, volume: str | None) -> str:
    volume = _format_solution_volume(volume)
    if not volume or "@" in dosage:
        return dosage
    return f"{dosage}@{volume}"


def _volume_ml_amount(volume: str | None) -> Decimal | None:
    formatted = _format_solution_volume(volume)
    if not formatted:
        return None
    return Decimal(formatted.removesuffix("ml"))


def _syringe_size_from_entry(syringe: dict[str, object] | None) -> str | None:
    if not syringe:
        return None
    size = syringe.get("size")
    return size if isinstance(size, str) else None


def _scale_dosage_by_volume(dosage: str, ratio: Decimal) -> str:
    match = DOSAGE_AMOUNT_RE.match(dosage)
    if not match:
        raise typer.BadParameter(f"Could not scale dosage by volume: {dosage}")
    amount = _parse_decimal_amount(match.group("amount"))
    scaled_amount = _format_decimal(amount * ratio)
    range_amount = match.group("range_amount")
    scaled_range = (
        f"-{_format_decimal(_parse_decimal_amount(range_amount) * ratio)}"
        if range_amount else ""
    )
    guess_marker = match.group("guess") or ""
    unit = match.group("unit")
    return f"{guess_marker}{scaled_amount}{scaled_range}{unit}{dosage[match.end():]}"


def _scale_dosages_by_volume(
    dosages: list[str],
    volume: str | None,
    syringe_size: str | None,
) -> list[str]:
    volume_ml = _volume_ml_amount(volume)
    syringe_ml = _volume_ml_amount(syringe_size)
    if volume_ml is None:
        raise typer.BadParameter("Dosage by volume requires a selected volume")
    if syringe_ml is None:
        raise typer.BadParameter("Dosage by volume requires a selected syringe size")
    if syringe_ml <= 0:
        raise typer.BadParameter("Syringe size must be greater than zero")
    ratio = volume_ml / syringe_ml
    return [_scale_dosage_by_volume(dosage, ratio) for dosage in dosages]


GAUGE_RE = re.compile(r"^\s*(?P<amount>\d+(?:\.\d+)?)\s*(?:g|ga|gauge)?\s*$", re.IGNORECASE)
NOTE_GAUGE_RE = re.compile(r"(?:^|\s)needle-gauge:(?P<gauge>[^\s]+)")
LENGTH_RE = re.compile(
    r"^\s*(?P<amount>\d+(?:\.\d+)?|\d+\s*/\s*\d+)\s*"
    r"(?P<unit>\"|in|inch|inches|mm|millimeter|millimeters|millimetre|millimetres|cm|centimeter|centimeters|centimetre|centimetres)?\s*$",
    re.IGNORECASE,
)
NOTE_LENGTH_RE = re.compile(r"(?:^|\s)needle-length:(?P<length>[^\s]+)")
NOTE_SYRINGE_SIZE_RE = re.compile(r"(?:^|\s)syringe-size:(?P<size>[^\s]+)")
NOTE_SYRINGE_KEY_RE = re.compile(r"(?:^|\s)syringe:(?P<key>[^\s]+)")
NOTE_NEEDLE_KEY_RE = re.compile(r"(?:^|\s)needle:(?P<key>[^\s]+)")
NOTE_REUSED_SYRINGE_RE = re.compile(r"(?:^|\s)reused-syringe(?:\s|$)")
NOTE_REUSED_NEEDLE_RE = re.compile(r"(?:^|\s)reused-needle(?:\s|$)")
NOTE_NON_DISPOSABLE_SYRINGE_RE = re.compile(r"(?:^|\s)non-disposable-syringe(?:\s|$)")
LENGTH_UNIT_ALIASES = {
    '"': "in",
    "in": "in",
    "inch": "in",
    "inches": "in",
    "mm": "mm",
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "cm": "cm",
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
}


def _format_needle_gauge(gauge: str | None) -> str | None:
    if not gauge:
        return None
    gauge = gauge.strip()
    if not gauge or _is_skip_choice(gauge):
        return None
    match = GAUGE_RE.match(gauge)
    if not match:
        raise typer.BadParameter(f"Could not parse '{gauge}' as a needle gauge")
    amount = match.group("amount")
    if "." in amount:
        amount = amount.rstrip("0").rstrip(".")
    return f"{amount}G"


def _append_note_needle_gauge(note: str | None, gauge: str | None) -> str | None:
    gauge = _format_needle_gauge(gauge)
    if not gauge:
        return note
    token = f"needle-gauge:{gauge}"
    if not note:
        return token
    if NOTE_GAUGE_RE.search(note):
        return note
    return f"{note} {token}"


def _format_needle_length(length: str | None) -> str | None:
    if not length:
        return None
    length = length.strip()
    if not length or _is_skip_choice(length):
        return None
    match = LENGTH_RE.match(length)
    if not match:
        raise typer.BadParameter(f"Could not parse '{length}' as a needle length")
    amount = re.sub(r"\s+", "", match.group("amount"))
    if "." in amount:
        amount = amount.rstrip("0").rstrip(".")
    unit = LENGTH_UNIT_ALIASES.get((match.group("unit") or "in").casefold())
    return f"{amount}{unit}"


def _append_note_needle_length(note: str | None, length: str | None) -> str | None:
    length = _format_needle_length(length)
    if not length:
        return note
    token = f"needle-length:{length}"
    if not note:
        return token
    if NOTE_LENGTH_RE.search(note):
        return note
    return f"{note} {token}"


def _append_note_syringe_size(note: str | None, syringe_size: str | None) -> str | None:
    syringe_size = _format_solution_volume(syringe_size)
    if not syringe_size:
        return note
    token = f"syringe-size:{syringe_size}"
    if not note:
        return token
    if NOTE_SYRINGE_SIZE_RE.search(note):
        return note
    return f"{note} {token}"


def _append_note_supply_key(note: str | None, label: str, key: str | None, pattern: re.Pattern) -> str | None:
    if not key:
        return note
    token = f"{label}:{key}"
    if not note:
        return token
    if pattern.search(note):
        return note
    return f"{note} {token}"


def _append_note_token(note: str | None, token: str, pattern: re.Pattern) -> str | None:
    if not note:
        return token
    if pattern.search(note):
        return note
    return f"{note} {token}"


def _reused_supply_entry(kind: str, entry: dict[str, object]) -> dict[str, object]:
    reused_entry = entry.copy()
    reused_entry["reuse"] = kind
    return reused_entry


def _is_reused_supply_entry(kind: str, entry: dict[str, object] | None) -> bool:
    return bool(entry and entry.get("reuse") == kind)


def _is_non_disposable_syringe_entry(entry: dict[str, object] | None) -> bool:
    if not entry:
        return False
    if not isinstance(entry.get("size"), str):
        return False
    if entry.get("non_disposable") is True:
        return True
    description = entry.get("description")
    return isinstance(description, str) and _alias_key(NON_DISPOSABLE_SYRINGE_TOKEN) in _alias_key(description)


def _append_note_injection_details(
    note: str | None,
    gauge: str | None,
    length: str | None,
    syringe_size: str | None,
) -> str | None:
    note = _append_note_needle_gauge(note, gauge)
    note = _append_note_needle_length(note, length)
    return _append_note_syringe_size(note, syringe_size)


def _append_note_supply_entries(
    note: str | None,
    syringe: dict[str, object] | None,
    needle: dict[str, object] | None,
) -> str | None:
    if syringe:
        if _is_reused_supply_entry("syringe", syringe):
            note = _append_note_token(note, "reused-syringe", NOTE_REUSED_SYRINGE_RE)
        if _is_non_disposable_syringe_entry(syringe):
            note = _append_note_token(note, "non-disposable-syringe", NOTE_NON_DISPOSABLE_SYRINGE_RE)
        syringe_key = _supply_entry_current_key("syringe", syringe)
        note = _append_note_supply_key(note, "syringe", syringe_key, NOTE_SYRINGE_KEY_RE)
        note = _append_note_syringe_size(note, syringe.get("size") if isinstance(syringe.get("size"), str) else None)
    if needle:
        if _is_reused_supply_entry("needle", needle):
            note = _append_note_token(note, "reused-needle", NOTE_REUSED_NEEDLE_RE)
        needle_key = _supply_entry_current_key("needle", needle)
        note = _append_note_supply_key(note, "needle", needle_key, NOTE_NEEDLE_KEY_RE)
        note = _append_note_needle_gauge(note, needle.get("gauge") if isinstance(needle.get("gauge"), str) else None)
        note = _append_note_needle_length(note, needle.get("length") if isinstance(needle.get("length"), str) else None)
    return note


SUPPLY_QUANTITY_RE = re.compile(r"^\s*(?P<count>\d+)\s*x\b\s*(?P<description>.+?)\s*$", re.IGNORECASE)


def _empty_supply_cache() -> dict[str, object]:
    return {"version": SUPPLY_CACHE_VERSION, "syringes": [], "needles": []}


def _valid_supply_entries(value) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    entries = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            count = int(item.get("count", 0))
        except (TypeError, ValueError):
            continue
        if count < 0:
            continue
        description = item.get("description")
        key = item.get("key")
        if not isinstance(description, str) or not isinstance(key, str):
            continue
        entry = {
            "key": key,
            "description": description,
            "count": count,
        }
        size = item.get("size")
        gauge = item.get("gauge")
        length = item.get("length")
        if isinstance(size, str):
            entry["size"] = size
        if isinstance(gauge, str):
            entry["gauge"] = gauge
        if isinstance(length, str):
            entry["length"] = length
        if item.get("non_disposable") is True and isinstance(size, str):
            entry["non_disposable"] = True
        entries.append(entry)
    return entries


def _load_supply_cache() -> dict[str, object]:
    if not SUPPLY_CACHE_FILE.exists():
        return _empty_supply_cache()
    try:
        with open(SUPPLY_CACHE_FILE) as infile:
            raw_cache = json.load(infile)
    except (OSError, json.JSONDecodeError):
        return _empty_supply_cache()
    if not isinstance(raw_cache, dict):
        return _empty_supply_cache()
    return {
        "version": SUPPLY_CACHE_VERSION,
        "syringes": _valid_supply_entries(raw_cache.get("syringes")),
        "needles": _valid_supply_entries(raw_cache.get("needles")),
    }


def _write_supply_cache(cache: dict[str, object]) -> None:
    SUPPLY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SUPPLY_CACHE_VERSION,
        "syringes": _valid_supply_entries(cache.get("syringes")),
        "needles": _valid_supply_entries(cache.get("needles")),
    }
    tmpfile = SUPPLY_CACHE_FILE.with_suffix(f"{SUPPLY_CACHE_FILE.suffix}.tmp")
    with open(tmpfile, "w") as outfile:
        json.dump(payload, outfile, indent=2, sort_keys=True)
        outfile.write("\n")
    tmpfile.replace(SUPPLY_CACHE_FILE)


def _parse_supply_quantity(value: str) -> tuple[int, str]:
    match = SUPPLY_QUANTITY_RE.match(value)
    if not match:
        raise typer.BadParameter("Supply must look like '<count>x <specs>', e.g. '100x 2.5ml luer-slip'")
    count = int(match.group("count"))
    if count <= 0:
        raise typer.BadParameter("Supply count must be greater than zero")
    return count, match.group("description").strip()


def _remaining_supply_description(tokens: list[str], used_indexes: set[int]) -> str:
    return " ".join(token for index, token in enumerate(tokens) if index not in used_indexes)


def _syringe_supply_key(size: str, remaining: str) -> str:
    return f"{size}:{_supply_key_part(remaining)}"


def _needle_supply_key(gauge: str, length: str, remaining: str) -> str:
    return f"{gauge}:{length}:{_supply_key_part(remaining)}"


def _syringe_supply_remaining(description: str, size: str) -> str:
    tokens = description.split()
    for index, token in enumerate(tokens):
        try:
            token_size = _format_solution_volume(token)
        except typer.BadParameter:
            continue
        if token_size == size:
            return _remaining_supply_description(tokens, {index})
    return description


def _needle_supply_remaining(description: str, gauge: str, length: str) -> str:
    tokens = description.split()
    gauge_index = None
    length_index = None
    for index, token in enumerate(tokens):
        if gauge_index is None:
            try:
                token_gauge = _format_needle_gauge(token)
            except typer.BadParameter:
                token_gauge = None
            if token_gauge == gauge:
                gauge_index = index
                continue
        if length_index is None:
            try:
                token_length = _format_needle_length(token)
            except typer.BadParameter:
                token_length = None
            if token_length == length:
                length_index = index
    return _remaining_supply_description(
        tokens,
        {index for index in (gauge_index, length_index) if index is not None},
    )


def _supply_entry_current_key(kind: str, entry: dict[str, object]) -> str | None:
    description = entry.get("description")
    if not isinstance(description, str):
        return None
    if kind == "syringe":
        size = entry.get("size")
        if not isinstance(size, str):
            return None
        return _syringe_supply_key(size, _syringe_supply_remaining(description, size))
    if kind == "needle":
        gauge = entry.get("gauge")
        length = entry.get("length")
        if not isinstance(gauge, str) or not isinstance(length, str):
            return None
        return _needle_supply_key(gauge, length, _needle_supply_remaining(description, gauge, length))
    return None


def _supply_entry_keys(entry: dict[str, object]) -> set[str]:
    keys = set()
    stored_key = entry.get("key")
    if isinstance(stored_key, str):
        keys.add(stored_key)
    syringe_key = _supply_entry_current_key("syringe", entry)
    if syringe_key:
        keys.add(syringe_key)
    needle_key = _supply_entry_current_key("needle", entry)
    if needle_key:
        keys.add(needle_key)
    return keys


def _parse_syringe_supply(value: str) -> dict[str, object]:
    count, description = _parse_supply_quantity(value)
    tokens = description.split()
    for index, token in enumerate(tokens):
        try:
            size = _format_solution_volume(token)
        except typer.BadParameter:
            continue
        if not size:
            continue
        remaining = _remaining_supply_description(tokens, {index})
        return {
            "key": _syringe_supply_key(size, remaining),
            "description": description,
            "count": count,
            "size": size,
            **({"non_disposable": True} if _alias_key(NON_DISPOSABLE_SYRINGE_TOKEN) in _alias_key(description) else {}),
        }
    raise typer.BadParameter(f"Could not find syringe size in '{value}'")


def _parse_needle_supply(value: str) -> dict[str, object]:
    count, description = _parse_supply_quantity(value)
    tokens = description.split()
    gauge = None
    gauge_index = None
    length = None
    length_index = None
    for index, token in enumerate(tokens):
        if gauge is None:
            try:
                gauge = _format_needle_gauge(token)
            except typer.BadParameter:
                pass
            else:
                if gauge:
                    gauge_index = index
                    continue
        if length is None:
            try:
                length = _format_needle_length(token)
            except typer.BadParameter:
                pass
            else:
                if length:
                    length_index = index
    if not gauge:
        raise typer.BadParameter(f"Could not find needle gauge in '{value}'")
    if not length:
        raise typer.BadParameter(f"Could not find needle length in '{value}'")
    used_indexes = {index for index in (gauge_index, length_index) if index is not None}
    remaining = _remaining_supply_description(tokens, used_indexes)
    return {
        "key": _needle_supply_key(gauge, length, remaining),
        "description": description,
        "count": count,
        "gauge": gauge,
        "length": length,
    }


def _add_supply_entries(kind: str, values: list[str], parser) -> list[dict[str, object]]:
    cache = _load_supply_cache()
    entries = _valid_supply_entries(cache.get(kind))
    changed_entries = []
    for value in values:
        parsed = parser(value)
        for entry in entries:
            if parsed["key"] not in _supply_entry_keys(entry):
                continue
            entry["key"] = parsed["key"]
            entry["count"] = int(entry["count"]) + int(parsed["count"])
            changed_entries.append(entry)
            break
        else:
            entries.append(parsed)
            changed_entries.append(parsed)
    cache[kind] = entries
    _write_supply_cache(cache)
    return changed_entries


def _remove_supply_entries(kind: str, values: list[str], parser) -> list[dict[str, object]]:
    cache = _load_supply_cache()
    entries = _valid_supply_entries(cache.get(kind))
    changed_entries = []
    for value in values:
        parsed = parser(value)
        for index, entry in enumerate(entries):
            if parsed["key"] not in _supply_entry_keys(entry):
                continue
            entry["key"] = parsed["key"]
            entry["count"] = max(0, int(entry["count"]) - int(parsed["count"]))
            changed_entries.append(entry.copy())
            if int(entry["count"]) == 0:
                entries.pop(index)
            break
        else:
            err_con.print(f"Supply not found: {parsed['description']}", style="bold yellow")
    cache[kind] = entries
    _write_supply_cache(cache)
    return changed_entries


def _available_supply_entries(kind: str) -> list[dict[str, object]]:
    return [
        entry
        for entry in _valid_supply_entries(_load_supply_cache().get(kind))
        if int(entry.get("count", 0)) > 0
    ]


def _supply_entry_label(entry: dict[str, object]) -> str:
    return f"{entry['description']} ({entry['count']} remaining)"


def _supply_reuse_action(label: str) -> str:
    return SUPPLY_ACTION_REUSE_SYRINGE if label.casefold() == "syringe" else SUPPLY_ACTION_REUSE_NEEDLE


def _parse_reused_supply_entry(kind: str, description: str) -> dict[str, object]:
    if kind == "syringes":
        entry = _parse_syringe_supply(f"1x {description}")
        entry.pop("count", None)
        return _reused_supply_entry("syringe", entry)
    if kind == "needles":
        entry = _parse_needle_supply(f"1x {description}")
        entry.pop("count", None)
        return _reused_supply_entry("needle", entry)
    raise typer.BadParameter(f"Unknown supply type: {kind}")


def _select_reused_supply_entry(mode: InputMode, label: str, kind: str) -> dict[str, object]:
    description = _select_supply_type(mode, kind, action="Reuse")
    return _parse_reused_supply_entry(kind, description)


def _prompt_for_supply_entry(label: str, kind: str) -> dict[str, object] | None:
    entries = _available_supply_entries(kind)
    reuse_action = _supply_reuse_action(label)
    con.print(f"{label}s:")
    labels = [SUPPLY_ACTION_SKIP, *[_supply_entry_label(entry) for entry in entries], reuse_action]
    for index, choice_label in enumerate(labels, 1):
        con.print(f"{index}. {choice_label}")
    selected = _prompt_for_value(label, "1", optional=False)
    try:
        selected_index = int(selected)
    except (TypeError, ValueError) as exc:
        selected_action = _resolve_alias(selected, SUPPLY_ACTION_ALIASES) or selected
        if selected_action == reuse_action:
            return _select_reused_supply_entry(InputMode.prompt, label, kind)
        if selected_action == SUPPLY_ACTION_SKIP:
            return None
        raise typer.BadParameter(f"{label} selection must be a number") from exc
    if selected_index < 1 or selected_index > len(labels):
        raise typer.BadParameter(f"{label} selection out of range")
    if labels[selected_index - 1] == SUPPLY_ACTION_SKIP:
        return None
    if labels[selected_index - 1] == reuse_action:
        return _select_reused_supply_entry(InputMode.prompt, label, kind)
    # Index 1 is the pinned Skip choice, so stack entries start at 2.
    return entries[selected_index - 2]


def _menu_for_supply_entry(executable: str, label: str, kind: str, allow_back: bool = False) -> dict[str, object] | None:
    entries = _available_supply_entries(kind)
    reuse_action = _supply_reuse_action(label)
    labels = [_supply_entry_label(entry) for entry in entries]
    choices = [SUPPLY_ACTION_SKIP, *labels, reuse_action]
    selected_label = _menu_for_value(
        executable,
        label,
        choices[0],
        candidates=choices,
        aliases=SUPPLY_ACTION_ALIASES,
        allow_back=allow_back,
    )
    selected_action = _resolve_alias(selected_label or "", SUPPLY_ACTION_ALIASES) or selected_label
    if selected_action == SUPPLY_ACTION_SKIP:
        return None
    if selected_action == reuse_action:
        return _select_reused_supply_entry(InputMode(executable), label, kind)
    for entry_label, entry in zip(labels, entries):
        if selected_label == entry_label:
            return entry
    raise typer.BadParameter(f"{label} selection is not in the stack")


def _note_needle_gauge(note: str | None) -> str | None:
    if not note:
        return None
    match = NOTE_GAUGE_RE.search(note)
    if not match:
        return None
    try:
        return _format_needle_gauge(match.group("gauge"))
    except typer.BadParameter:
        return None


def _note_needle_length(note: str | None) -> str | None:
    if not note:
        return None
    match = NOTE_LENGTH_RE.search(note)
    if not match:
        return None
    try:
        return _format_needle_length(match.group("length"))
    except typer.BadParameter:
        return None


def _note_syringe_size(note: str | None) -> str | None:
    if not note:
        return None
    match = NOTE_SYRINGE_SIZE_RE.search(note)
    if not match:
        return None
    try:
        return _format_solution_volume(match.group("size"))
    except typer.BadParameter:
        return None


def _note_supply_key(note: str | None, pattern: re.Pattern) -> str | None:
    if not note:
        return None
    match = pattern.search(note)
    if not match:
        return None
    return match.group("key")


def _note_has_token(note: str | None, pattern: re.Pattern) -> bool:
    return bool(note and pattern.search(note))


def _decrement_matching_supply(entries: list[dict[str, object]], **specs: str) -> dict[str, object] | None:
    for entry in entries:
        if any(entry.get(key) != value for key, value in specs.items()):
            continue
        if _is_non_disposable_syringe_entry(entry):
            continue
        if int(entry.get("count", 0)) <= 0:
            continue
        entry["count"] = int(entry["count"]) - 1
        return entry
    return None


def _decrement_supply_key(entries: list[dict[str, object]], key: str | None) -> dict[str, object] | None:
    if not key:
        return None
    for entry in entries:
        if key not in _supply_entry_keys(entry):
            continue
        if _is_non_disposable_syringe_entry(entry):
            continue
        if int(entry.get("count", 0)) <= 0:
            continue
        entry["count"] = int(entry["count"]) - 1
        return entry
    return None


def _decrement_logged_supplies(note: str | None) -> list[dict[str, object]]:
    reused_syringe = _note_has_token(note, NOTE_REUSED_SYRINGE_RE)
    non_disposable_syringe = _note_has_token(note, NOTE_NON_DISPOSABLE_SYRINGE_RE)
    reused_needle = _note_has_token(note, NOTE_REUSED_NEEDLE_RE)
    syringe_key = _note_supply_key(note, NOTE_SYRINGE_KEY_RE)
    needle_key = _note_supply_key(note, NOTE_NEEDLE_KEY_RE)
    syringe_size = None if reused_syringe or non_disposable_syringe else _note_syringe_size(note)
    needle_gauge = None if reused_needle else _note_needle_gauge(note)
    needle_length = None if reused_needle else _note_needle_length(note)
    if reused_syringe or non_disposable_syringe:
        syringe_key = None
    if reused_needle:
        needle_key = None
    if not syringe_key and not needle_key and not syringe_size and not (needle_gauge and needle_length):
        return []

    cache = _load_supply_cache()
    changed_entries = []
    if syringe_key or syringe_size:
        syringes = _valid_supply_entries(cache.get("syringes"))
        cache["syringes"] = syringes
        changed = _decrement_supply_key(syringes, syringe_key)
        if not changed and syringe_size:
            changed = _decrement_matching_supply(
                syringes,
                size=syringe_size,
            )
        if changed:
            changed_entries.append(changed)
    if needle_key or (needle_gauge and needle_length):
        needles = _valid_supply_entries(cache.get("needles"))
        cache["needles"] = needles
        changed = _decrement_supply_key(needles, needle_key)
        if not changed and needle_gauge and needle_length:
            changed = _decrement_matching_supply(
                needles,
                gauge=needle_gauge,
                length=needle_length,
            )
        if changed:
            changed_entries.append(changed)
    if changed_entries:
        _write_supply_cache(cache)
    return changed_entries


def _print_supply_updates(prefix: str, entries: list[dict[str, object]]) -> None:
    for entry in entries:
        con.print(f"{prefix}: {entry['description']} ({entry['count']} remaining)")


def _print_supply_stock() -> None:
    cache = _load_supply_cache()
    syringes = _valid_supply_entries(cache.get("syringes"))
    needles = _valid_supply_entries(cache.get("needles"))
    if not syringes and not needles:
        con.print("No supply stock found.")
        return

    if syringes:
        con.print("Syringes:")
        for entry in syringes:
            con.print(f"- {entry['description']}: {entry['count']}")
    if needles:
        con.print("Needles:")
        for entry in needles:
            con.print(f"- {entry['description']}: {entry['count']}")


def _empty_stash_cache() -> dict[str, object]:
    return {"version": STASH_CACHE_VERSION, "entries": []}


def _valid_stash_entries(value) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    entries = []
    for item in value:
        if not isinstance(item, dict):
            continue
        substance = item.get("substance")
        unit = item.get("unit")
        if not isinstance(substance, str) or not isinstance(unit, str):
            continue
        try:
            amount = Decimal(str(item.get("amount", "0")))
        except InvalidOperation:
            continue
        if amount < 0:
            continue
        entry = {
            "key": item.get("key") if isinstance(item.get("key"), str) else _stash_key(substance, unit),
            "substance": substance,
            "amount": _format_decimal(amount),
            "unit": unit,
        }
        entries.append(entry)
    return entries


def _load_stash_cache() -> dict[str, object]:
    if not STASH_CACHE_FILE.exists():
        return _empty_stash_cache()
    try:
        with open(STASH_CACHE_FILE) as infile:
            raw_cache = json.load(infile)
    except (OSError, json.JSONDecodeError):
        return _empty_stash_cache()
    if not isinstance(raw_cache, dict):
        return _empty_stash_cache()
    return {
        "version": STASH_CACHE_VERSION,
        "entries": _valid_stash_entries(raw_cache.get("entries")),
    }


def _write_stash_cache(cache: dict[str, object]) -> None:
    STASH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STASH_CACHE_VERSION,
        "entries": _valid_stash_entries(cache.get("entries")),
    }
    tmpfile = STASH_CACHE_FILE.with_suffix(f"{STASH_CACHE_FILE.suffix}.tmp")
    with open(tmpfile, "w") as outfile:
        json.dump(payload, outfile, indent=2, sort_keys=True)
        outfile.write("\n")
    tmpfile.replace(STASH_CACHE_FILE)


def _stash_key(substance: str, unit: str) -> str:
    return f"{_supply_key_part(_resolve_alias(substance, SUBSTANCE_ALIASES) or substance)}:{unit.casefold()}"


def _stash_entry_keys(entry: dict[str, object]) -> set[str]:
    keys = set()
    stored_key = entry.get("key")
    if isinstance(stored_key, str):
        keys.add(stored_key)
    substance = entry.get("substance")
    unit = entry.get("unit")
    if isinstance(substance, str) and isinstance(unit, str):
        keys.add(_stash_key(substance, unit))
        resolved = _resolve_alias(substance, SUBSTANCE_ALIASES)
        if resolved:
            keys.add(_stash_key(resolved, unit))
    return keys


def _parse_stash_quantity(value: str) -> dict[str, object]:
    match = DOSAGE_AMOUNT_RE.match(value)
    if not match or match.group("range_amount"):
        raise typer.BadParameter("Stash must look like '<amount><unit> <substance>', e.g. '500mg Ketamine'")
    try:
        amount = Decimal(match.group("amount"))
    except InvalidOperation as exc:
        raise typer.BadParameter(f"Could not parse stash amount: {match.group('amount')}") from exc
    if amount <= 0:
        raise typer.BadParameter("Stash amount must be greater than zero")
    normalized = _normalize_usage_unit(amount, match.group("unit"))
    if normalized is None:
        raise typer.BadParameter(f"Unsupported stash unit: {match.group('unit')}")
    amount, unit = normalized
    substance = value[match.end():].strip()
    if not substance:
        raise typer.BadParameter("Stash substance is required")
    substance = _resolve_alias(substance, SUBSTANCE_ALIASES) or substance
    return {
        "key": _stash_key(substance, unit),
        "substance": substance,
        "amount": _format_decimal(amount),
        "unit": unit,
    }


def _change_stash_entries(values: list[str], sign: int) -> list[dict[str, object]]:
    cache = _load_stash_cache()
    entries = _valid_stash_entries(cache.get("entries"))
    changed_entries = []
    for value in values:
        parsed = _parse_stash_quantity(value)
        parsed_amount = Decimal(parsed["amount"])
        for index, entry in enumerate(entries):
            if parsed["key"] not in _stash_entry_keys(entry):
                continue
            amount = Decimal(entry["amount"]) + (parsed_amount * sign)
            entry["key"] = parsed["key"]
            entry["substance"] = parsed["substance"]
            entry["amount"] = _format_decimal(max(Decimal("0"), amount))
            entry["unit"] = parsed["unit"]
            changed_entries.append(entry.copy())
            if Decimal(entry["amount"]) == 0:
                entries.pop(index)
            break
        else:
            if sign < 0:
                err_con.print(f"Stash not found: {parsed['substance']} ({parsed['unit']})", style="bold yellow")
            else:
                entries.append(parsed)
                changed_entries.append(parsed)
    cache["entries"] = entries
    _write_stash_cache(cache)
    return changed_entries


def _add_stash_entries(values: list[str]) -> list[dict[str, object]]:
    return _change_stash_entries(values, 1)


def _remove_stash_entries(values: list[str]) -> list[dict[str, object]]:
    return _change_stash_entries(values, -1)


def _print_stash_updates(prefix: str, entries: list[dict[str, object]]) -> None:
    for entry in entries:
        con.print(f"{prefix}: {entry['substance']} ({_format_stash_amount(entry['amount'])}{entry['unit']} remaining)")


def _print_stash() -> None:
    entries = _valid_stash_entries(_load_stash_cache().get("entries"))
    if not entries:
        con.print("No stash found.")
        return
    con.print("Stash:")
    for entry in sorted(entries, key=lambda item: (str(item["substance"]).casefold(), str(item["unit"]))):
        con.print(f"- {entry['substance']}: {_format_stash_amount(entry['amount'])}{entry['unit']}")


def _decrement_logged_stash(substance: str, dosage: str) -> list[dict[str, object]]:
    cache = _load_stash_cache()
    entries = _valid_stash_entries(cache.get("entries"))
    changed_entries = []
    dosages = _split_mixture_values(_dosage_without_volume(dosage))
    substances = _split_mixture_values_for_count(substance, len(dosages))
    substances_by_index = substances if isinstance(substances, list) else _split_mixture_values(substances)
    for logged_substance, logged_dosage in zip(substances_by_index, dosages):
        parsed_dosage = _parse_usage_dosage(logged_dosage)
        if parsed_dosage is None:
            continue
        amount, unit = parsed_dosage
        key = _stash_key(logged_substance, unit)
        for entry in entries:
            if key not in _stash_entry_keys(entry):
                continue
            remaining = max(Decimal("0"), Decimal(entry["amount"]) - amount)
            entry["amount"] = _format_decimal(remaining)
            changed_entries.append(entry.copy())
            break
    if changed_entries:
        cache["entries"] = [entry for entry in entries if Decimal(entry["amount"]) > 0]
        _write_stash_cache(cache)
    return changed_entries


def _default_config_file() -> Path:
    return app_dir / "config.toml"


def _config_file_from_option(config: Path | None) -> Path:
    return config.expanduser() if config else _default_config_file()


def _toml_quote(value: str) -> str:
    return json.dumps(value)


def _raw_config_value(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _format_toml_value(value: str, field) -> str:
    if field.annotation is bool:
        return "true" if value.casefold() in ("1", "true", "yes", "on") else "false"
    return _toml_quote(value)


def _load_raw_config(config_file: Path) -> dict[str, str]:
    if not config_file.exists():
        return {}
    raw = toml_loader(config_file)
    return {
        key: str(value)
        for key, value in raw.items()
        if key in LogConfig.model_fields and value is not None
    }


def _write_raw_config(config_file: Path, values: dict[str, str]) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{key} = {_format_toml_value(value, LogConfig.model_fields[key])}"
        for key, value in values.items()
        if key in LogConfig.model_fields and value != ""
    ]
    config_file.write_text("\n".join(lines) + ("\n" if lines else ""))


def _format_setting_choice(name: str, value: str | None) -> str:
    return f"{name}=\"{value}\"" if value not in (None, "") else f"{name} (unset)"


def _edit_settings(mode: InputMode, config_file: Path, current_values: dict[str, object]) -> None:
    values = _load_raw_config(config_file)
    for key in LogConfig.model_fields:
        values.setdefault(key, _raw_config_value(current_values.get(key)) or "")

    while True:
        keys = list(LogConfig.model_fields)
        choices = [*keys, BACK_CHOICE]
        labels_by_value = {
            key: _format_setting_choice(key, values.get(key))
            for key in keys
        }
        if mode == InputMode.prompt:
            labels = [labels_by_value.get(choice, choice) for choice in choices]
            _print_prompt_choices("Settings", labels)
            selected = _prompt_for_value("Settings", labels[0], optional=False)
            try:
                selected_index = int(selected)
            except (TypeError, ValueError):
                selected_key = selected
                for choice, label in zip(choices, labels):
                    if selected == label or selected == choice:
                        selected_key = choice
                        break
            else:
                if selected_index < 1 or selected_index > len(choices):
                    raise typer.BadParameter("Settings selection out of range")
                selected_key = choices[selected_index - 1]
        else:
            selected_key = _menu_for_value(
                mode.value,
                "Settings",
                choices[0],
                candidates=choices,
                labels_by_value=labels_by_value,
            )
        if selected_key == BACK_CHOICE:
            return
        if selected_key not in LogConfig.model_fields:
            raise typer.BadParameter(f"Unknown setting: {selected_key}")

        new_value = _prompt_for_value(
            selected_key,
            values.get(selected_key) or "",
            optional=True,
        ) if mode == InputMode.prompt else _menu_for_value(
            mode.value,
            selected_key,
            values.get(selected_key) or "",
            candidates=[values.get(selected_key)] if values.get(selected_key) else [],
            optional=True,
        )
        values[selected_key] = new_value or ""
        _write_raw_config(config_file, values)
        con.print(f"Updated {selected_key}.")


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


def _select_prompt_action(mode: InputMode) -> str:
    return _interactive_choice(
        mode,
        "Action",
        ACTION_LOG_INGESTION,
        [ACTION_LOG_INGESTION, ACTION_EDIT_SUPPLY_STACK, ACTION_EDIT_STASH, ACTION_HISTORY, ACTION_STATISTICS, ACTION_USERPAGE, ACTION_SETTINGS],
        ACTION_ALIASES,
    )


def _resolve_base_prompt_action(
    log_ingestion_action: bool,
    stack_menu_action: bool,
    stash_menu_action: bool,
) -> str | None:
    if sum(1 for value in (log_ingestion_action, stack_menu_action, stash_menu_action) if value) > 1:
        raise typer.BadParameter("Choose only one base prompt action")
    if log_ingestion_action:
        return ACTION_LOG_INGESTION
    if stack_menu_action:
        return ACTION_EDIT_SUPPLY_STACK
    if stash_menu_action:
        return ACTION_EDIT_STASH
    return None


def _supply_stack_entries() -> list[tuple[str, dict[str, object]]]:
    cache = _load_supply_cache()
    entries: list[tuple[str, dict[str, object]]] = []
    for kind in ("syringes", "needles"):
        for entry in _valid_supply_entries(cache.get(kind)):
            entries.append((kind, entry))
    return entries


def _supply_stack_item_id(kind: str, entry: dict[str, object]) -> str:
    key = _supply_entry_current_key("syringe" if kind == "syringes" else "needle", entry)
    return f"{kind}:{key or entry.get('key') or entry.get('description')}"


def _supply_stack_item_label(kind: str, entry: dict[str, object]) -> str:
    noun = "Syringe" if kind == "syringes" else "Needle"
    return f"{noun}: {entry['description']} ({entry['count']})"


def _select_stack_item(mode: InputMode) -> tuple[str, dict[str, object]] | None:
    entries = _supply_stack_entries()
    item_by_id = {
        _supply_stack_item_id(kind, entry): (kind, entry)
        for kind, entry in entries
    }
    choices = [SUPPLY_ACTION_ADD_NEW, *item_by_id, SUPPLY_ACTION_BACK]
    labels_by_value = {
        _supply_stack_item_id(kind, entry): _supply_stack_item_label(kind, entry)
        for kind, entry in entries
    }
    labels_by_value.update({SUPPLY_ACTION_ADD_NEW: SUPPLY_ACTION_ADD_NEW, SUPPLY_ACTION_BACK: SUPPLY_ACTION_BACK})
    if mode == InputMode.prompt:
        labels = [labels_by_value.get(choice, choice) for choice in choices]
        _print_prompt_choices("Stack", labels)
        value = _prompt_for_value("Stack", labels[0] if labels else SUPPLY_ACTION_ADD_NEW, optional=False)
        try:
            selected_index = int(value)
        except (TypeError, ValueError):
            selected = _resolve_alias(value, SUPPLY_ACTION_ALIASES) or value
            if selected == SUPPLY_ACTION_ADD_NEW:
                return None
            if selected == SUPPLY_ACTION_BACK:
                raise StopIteration
            for choice, label in zip(choices, labels):
                if choice in item_by_id and (selected == choice or selected == label):
                    return item_by_id[choice]
            raise typer.BadParameter(f"Unknown stack item: {value}")
        if selected_index < 1 or selected_index > len(choices):
            raise typer.BadParameter("Stack selection out of range")
        choice = choices[selected_index - 1]
    else:
        choice = _menu_for_value(
            mode.value,
            "Stack",
            choices[0] if choices else SUPPLY_ACTION_ADD_NEW,
            candidates=choices,
            labels_by_value=labels_by_value,
            aliases=SUPPLY_ACTION_ALIASES,
        )
    if choice == SUPPLY_ACTION_BACK:
        raise StopIteration
    if choice == SUPPLY_ACTION_ADD_NEW:
        return None
    if not choice or choice not in item_by_id:
        raise typer.BadParameter(f"Unknown stack item: {choice}")
    return item_by_id[choice]


def _prompt_for_supply_spec(label: str) -> str:
    return _prompt_for_value(label, None, optional=False) or ""


def _menu_for_supply_spec(executable: str, label: str) -> str:
    return _menu_for_value(executable, label, None, optional=False) or ""


def _interactive_supply_spec(mode: InputMode, label: str) -> str:
    if mode == InputMode.prompt:
        return _prompt_for_supply_spec(label)
    return _menu_for_supply_spec(mode.value, label)


def _interactive_quantity(
    mode: InputMode,
    label: str,
    fixed_choices: list[int],
    max_value: int | None = None,
) -> int:
    choices = [str(choice) for choice in fixed_choices]
    if mode == InputMode.prompt:
        _print_prompt_choices(label, choices)
        raw_value = _prompt_for_value(label, choices[0], optional=False)
    else:
        raw_value = _menu_for_value(
            mode.value,
            label,
            choices[0],
            candidates=choices,
        )
    try:
        amount = int(raw_value or "")
    except ValueError as exc:
        raise typer.BadParameter(f"{label} must be a number") from exc
    if amount <= 0:
        raise typer.BadParameter(f"{label} must be greater than zero")
    if max_value is not None and amount > max_value:
        raise typer.BadParameter(f"{label} cannot be greater than {max_value}")
    return amount


def _change_supply_stack_entry(kind: str, target_entry: dict[str, object], delta: int) -> dict[str, object]:
    cache = _load_supply_cache()
    entries = _valid_supply_entries(cache.get(kind))
    target_keys = _supply_entry_keys(target_entry)
    for index, entry in enumerate(entries):
        if _supply_entry_keys(entry).isdisjoint(target_keys):
            continue
        entry["count"] = max(0, int(entry.get("count", 0)) + delta)
        if int(entry["count"]) == 0:
            removed = entry.copy()
            entries.pop(index)
            cache[kind] = entries
            _write_supply_cache(cache)
            return removed
        cache[kind] = entries
        _write_supply_cache(cache)
        return entry
    raise typer.BadParameter(f"Supply not found: {target_entry.get('description')}")


def _known_supply_descriptions(kind: str) -> list[str]:
    seen = set()
    descriptions = []
    for _, entry in _supply_stack_entries():
        if kind == "syringes" and "size" not in entry:
            continue
        if kind == "needles" and ("gauge" not in entry or "length" not in entry):
            continue
        description = entry.get("description")
        if not isinstance(description, str):
            continue
        key = _alias_key(description)
        if key in seen:
            continue
        seen.add(key)
        descriptions.append(description)
    return descriptions


def _select_supply_type(mode: InputMode, kind: str, action: str = "Add") -> str:
    if kind == "syringes":
        label = f"{action} syringe"
        custom = SUPPLY_ACTION_SPECIFY_NEW_SYRINGE
    else:
        label = f"{action} needle"
        custom = SUPPLY_ACTION_SPECIFY_NEW_NEEDLE
    choices = [*_known_supply_descriptions(kind), custom]
    if mode == InputMode.prompt:
        _print_prompt_choices(label, choices)
        value = _prompt_for_value(label, choices[0] if choices else custom, optional=False)
        try:
            selected_index = int(value)
        except (TypeError, ValueError):
            selected = _resolve_alias(value, SUPPLY_ACTION_ALIASES) or value
        else:
            if selected_index < 1 or selected_index > len(choices):
                raise typer.BadParameter(f"{label} selection out of range")
            selected = choices[selected_index - 1]
    else:
        selected = _menu_for_value(
            mode.value,
            label,
            choices[0] if choices else custom,
            candidates=choices,
            aliases=SUPPLY_ACTION_ALIASES,
            labels_by_value={choice: choice for choice in choices},
        )
    if selected == custom:
        return _interactive_supply_spec(mode, "Syringe Supply" if kind == "syringes" else "Needle Supply")
    if not selected:
        raise typer.BadParameter(f"{label} is required")
    return selected


def _add_new_supply_stack_entry(mode: InputMode) -> None:
    action = _interactive_choice(
        mode,
        SUPPLY_ACTION_ADD_NEW,
        SUPPLY_ACTION_ADD_SYRINGE,
        [SUPPLY_ACTION_ADD_SYRINGE, SUPPLY_ACTION_ADD_NEEDLE, SUPPLY_ACTION_BACK],
        SUPPLY_ACTION_ALIASES,
    )
    if action == SUPPLY_ACTION_BACK:
        return
    if action == SUPPLY_ACTION_ADD_SYRINGE:
        description = _select_supply_type(mode, "syringes")
        amount = _interactive_quantity(mode, "Add", [1, 5, 10, 50, 100])
        _print_supply_updates("Added syringe supply", _add_supply_entries("syringes", [f"{amount}x {description}"], _parse_syringe_supply))
        return
    if action == SUPPLY_ACTION_ADD_NEEDLE:
        description = _select_supply_type(mode, "needles")
        amount = _interactive_quantity(mode, "Add", [1, 5, 10, 50, 100])
        _print_supply_updates("Added needle supply", _add_supply_entries("needles", [f"{amount}x {description}"], _parse_needle_supply))
        return
    raise typer.BadParameter(f"Unknown supply action: {action}")


def _edit_supply_stack(mode: InputMode) -> None:
    while True:
        try:
            selected = _select_stack_item(mode)
        except StopIteration:
            return
        if selected is None:
            _add_new_supply_stack_entry(mode)
            continue
        kind, entry = selected
        action = _interactive_choice(
            mode,
            _supply_stack_item_label(kind, entry),
            SUPPLY_ACTION_ADD,
            [SUPPLY_ACTION_ADD, SUPPLY_ACTION_REMOVE, SUPPLY_ACTION_BACK],
            SUPPLY_ACTION_ALIASES,
        )
        if action == SUPPLY_ACTION_BACK:
            continue
        if action == SUPPLY_ACTION_ADD:
            amount = _interactive_quantity(mode, "Add", [1, 5, 10, 50, 100])
            changed = _change_supply_stack_entry(kind, entry, amount)
            _print_supply_updates("Added supply", [changed])
            continue
        if action == SUPPLY_ACTION_REMOVE:
            current_count = int(entry.get("count", 0))
            amount = _interactive_quantity(mode, "Remove", [1], max_value=current_count)
            changed = _change_supply_stack_entry(kind, entry, -amount)
            _print_supply_updates("Removed supply", [changed])
            continue
        raise typer.BadParameter(f"Unknown supply action: {action}")


def _stash_entries() -> list[dict[str, object]]:
    return _valid_stash_entries(_load_stash_cache().get("entries"))


def _stash_item_id(entry: dict[str, object]) -> str:
    return str(entry.get("key") or _stash_key(str(entry["substance"]), str(entry["unit"])))


def _stash_item_label(entry: dict[str, object]) -> str:
    return f"{entry['substance']} ({_format_stash_amount(entry['amount'])}{entry['unit']})"


def _select_stash_item(mode: InputMode) -> dict[str, object] | None:
    entries = _stash_entries()
    item_by_id = {
        _stash_item_id(entry): entry
        for entry in entries
    }
    choices = [SUPPLY_ACTION_ADD_NEW, *item_by_id, SUPPLY_ACTION_BACK]
    labels_by_value = {
        _stash_item_id(entry): _stash_item_label(entry)
        for entry in entries
    }
    labels_by_value.update({SUPPLY_ACTION_ADD_NEW: SUPPLY_ACTION_ADD_NEW, SUPPLY_ACTION_BACK: SUPPLY_ACTION_BACK})
    if mode == InputMode.prompt:
        labels = [labels_by_value.get(choice, choice) for choice in choices]
        _print_prompt_choices("Stash", labels)
        value = _prompt_for_value("Stash", labels[0] if labels else SUPPLY_ACTION_ADD_NEW, optional=False)
        try:
            selected_index = int(value)
        except (TypeError, ValueError):
            selected = _resolve_alias(value, SUPPLY_ACTION_ALIASES) or value
            if selected == SUPPLY_ACTION_ADD_NEW:
                return None
            if selected == SUPPLY_ACTION_BACK:
                raise StopIteration
            for choice, label in zip(choices, labels):
                if choice in item_by_id and (selected == choice or selected == label):
                    return item_by_id[choice]
            raise typer.BadParameter(f"Unknown stash item: {value}")
        if selected_index < 1 or selected_index > len(choices):
            raise typer.BadParameter("Stash selection out of range")
        choice = choices[selected_index - 1]
    else:
        choice = _menu_for_value(
            mode.value,
            "Stash",
            choices[0] if choices else SUPPLY_ACTION_ADD_NEW,
            candidates=choices,
            labels_by_value=labels_by_value,
            aliases=SUPPLY_ACTION_ALIASES,
        )
    if choice == SUPPLY_ACTION_BACK:
        raise StopIteration
    if choice == SUPPLY_ACTION_ADD_NEW:
        return None
    if not choice or choice not in item_by_id:
        raise typer.BadParameter(f"Unknown stash item: {choice}")
    return item_by_id[choice]


def _interactive_stash_amount(mode: InputMode, label: str) -> str:
    if mode == InputMode.prompt:
        return _prompt_for_dosage(label)
    return _menu_for_dosage(mode.value, label)


def _interactive_stash_substance(mode: InputMode, current: str | None = None) -> str:
    if mode == InputMode.prompt:
        return _prompt_for_value("Substance", current, optional=False) or ""
    return _menu_for_value(mode.value, "Substance", current, optional=False) or ""


def _add_new_stash_entry(mode: InputMode) -> None:
    substance = _interactive_stash_substance(mode)
    amount = _interactive_stash_amount(mode, "Add")
    _print_stash_updates("Added stash", _add_stash_entries([f"{amount} {substance}"]))


def _change_stash_entry(entry: dict[str, object], amount: str, sign: int) -> dict[str, object] | None:
    value = f"{amount} {entry['substance']}"
    changed = _add_stash_entries([value]) if sign > 0 else _remove_stash_entries([value])
    return changed[0] if changed else None


def _edit_stash(mode: InputMode) -> None:
    while True:
        try:
            selected = _select_stash_item(mode)
        except StopIteration:
            return
        if selected is None:
            _add_new_stash_entry(mode)
            continue
        action = _interactive_choice(
            mode,
            _stash_item_label(selected),
            SUPPLY_ACTION_ADD,
            [SUPPLY_ACTION_ADD, SUPPLY_ACTION_REMOVE, SUPPLY_ACTION_BACK],
            SUPPLY_ACTION_ALIASES,
        )
        if action == SUPPLY_ACTION_BACK:
            continue
        if action == SUPPLY_ACTION_ADD:
            amount = _interactive_stash_amount(mode, "Add")
            changed = _change_stash_entry(selected, amount, 1)
            _print_stash_updates("Added stash", [changed] if changed else [])
            continue
        if action == SUPPLY_ACTION_REMOVE:
            amount = _interactive_stash_amount(mode, "Remove")
            changed = _change_stash_entry(selected, amount, -1)
            _print_stash_updates("Removed stash", [changed] if changed else [])
            continue
        raise typer.BadParameter(f"Unknown stash action: {action}")


def _split_dosage_unit(current: str | None) -> tuple[str | None, str | None]:
    if not current:
        return None, None
    stripped = current.strip()
    for unit in sorted(UNIT_CHOICES, key=len, reverse=True):
        if stripped.casefold().endswith(unit.casefold()):
            amount = stripped[:-len(unit)].strip()
            return amount or stripped, unit
    return stripped, None


def _split_dosage_guess_marker(dosage: str | None) -> tuple[bool, str | None]:
    if dosage is None:
        return False, None
    dosage = dosage.strip()
    if dosage.startswith("~"):
        return True, dosage[1:].lstrip()
    return False, dosage


def _strip_dosage_guess_marker(dosage: str | None) -> str | None:
    _, stripped = _split_dosage_guess_marker(dosage)
    return stripped


def _mark_guess_dosage(dosage: str) -> str:
    dosage = _strip_dosage_guess_marker(dosage) or ""
    return f"~{dosage}" if dosage else dosage


def _eval_decimal_math_expression(value: str) -> Decimal:
    def eval_node(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = eval_node(node.operand)
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left = eval_node(node.left)
            right = eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise typer.BadParameter("Dosage expression cannot divide by zero")
            return left / right
        raise typer.BadParameter(f"Invalid dosage expression: {value}")

    try:
        parsed = ast.parse(value, mode="eval")
    except SyntaxError as exc:
        raise typer.BadParameter(f"Invalid dosage expression: {value}") from exc
    return eval_node(parsed)


DOSAGE_RANGE_AMOUNT_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*-\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)$"
)


def _is_dosage_math_expression(amount: str) -> bool:
    amount = amount.strip()
    if not re.search(r"[+\-*/()]", amount):
        return False
    return not DOSAGE_RANGE_AMOUNT_RE.match(amount)


def _format_dosage_amount(amount: str) -> str:
    is_approximate, amount = _split_dosage_guess_marker(amount)
    amount = (amount or "").strip()
    if _is_dosage_math_expression(amount):
        amount = _format_decimal(_eval_decimal_math_expression(amount))
    return _mark_guess_dosage(amount) if is_approximate else amount


def _format_dosage_expression(dosage: str) -> str:
    """Evaluate a math expression in a complete dosage such as '~25*2mg@1ml' to '~50mg@1ml'."""
    dosage = dosage.strip()
    if "@" in dosage:
        before, after = [part.strip() for part in dosage.split("@", 1)]
        if before.endswith("ml") and after:
            return f"{before}@{_format_dosage_expression(after)}"
        return f"{_format_dosage_expression(before)}@{after}"
    is_approximate, stripped = _split_dosage_guess_marker(dosage)
    stripped = (stripped or "").strip()
    amount, unit = _split_dosage_unit(stripped)
    if not amount or _is_custom_dosage(amount) or not _is_dosage_math_expression(amount):
        return dosage
    formatted = f"{_format_dosage_amount(amount)}{unit or ''}"
    return _mark_guess_dosage(formatted) if is_approximate else formatted


def _format_dosage(amount: str, unit: str | None) -> str:
    amount = _format_dosage_amount(amount)
    if _is_custom_dosage(amount):
        return amount
    return f"{amount}{unit or 'mg'}"


def _is_custom_dosage(amount: str) -> bool:
    return any(char.isalpha() for char in amount)


def _prompt_for_dosage(label: str, current: str | None = None, optional: bool = False) -> str:
    current_amount, current_unit = _split_dosage_unit(_strip_dosage_guess_marker(current))
    raw_amount = _prompt_for_value(label, current_amount, optional=optional)
    if raw_amount is None:
        return ""
    is_approximate, amount = _split_dosage_guess_marker(raw_amount)
    amount = amount or ""
    if _is_custom_dosage(amount):
        dosage = amount.strip()
    else:
        unit = _prompt_for_value("Unit", current_unit or "mg", optional=False)
        dosage = _format_dosage(amount, unit)
    if is_approximate:
        return _mark_guess_dosage(dosage)
    if not typer.confirm("Measured dosage?", default=True):
        return _mark_guess_dosage(dosage)
    return dosage


def _menu_for_dosage(
    executable: str,
    label: str,
    current: str | None = None,
    optional: bool = False,
    candidates: list[str] | None = None,
    allow_back: bool = False,
) -> str:
    current_amount, current_unit = _split_dosage_unit(current)
    dosage_candidates = []
    if current:
        dosage_candidates.append(current)
    dosage_candidates.extend(candidates or [])
    while True:
        raw_amount = _menu_for_value(
            executable,
            label,
            current_amount,
            candidates=dosage_candidates,
            optional=optional,
            allow_back=allow_back,
            keep_custom_prefix=True,
        )
        if raw_amount is None:
            return ""
        is_approximate, amount = _split_dosage_guess_marker(raw_amount)
        amount = amount or ""
        selected_amount, selected_unit = _split_dosage_unit(amount)
        if selected_unit:
            dosage = _format_dosage(selected_amount or amount, selected_unit)
            return _mark_guess_dosage(dosage) if is_approximate else dosage
        if _is_custom_dosage(amount):
            return _mark_guess_dosage(amount.strip()) if is_approximate else amount.strip()
        unit_candidates = [current_unit] if current_unit else []
        unit_candidates.extend(UNIT_CHOICES)
        try:
            unit = _menu_for_value(
                executable,
                "Unit",
                current_unit or "mg",
                candidates=unit_candidates,
                allow_back=allow_back,
            )
        except BackRequested:
            continue
        dosage = _format_dosage(amount, unit)
        return _mark_guess_dosage(dosage) if is_approximate else dosage


def _prompt_for_extra_info(
    note: str | None,
    time: str | None,
    allow_dosage_by_volume: bool = False,
) -> tuple[str | None, str | None, bool]:
    if not typer.confirm(EXTRA_INFO_PROMPT, default=False):
        return note, time, False

    selected_note = _prompt_for_value("Note", note, optional=True)
    selected_time = _prompt_for_time_details(time)
    dosage_by_volume = (
        typer.confirm("Dosage by volume?", default=False)
        if allow_dosage_by_volume else False
    )
    return selected_note, selected_time, dosage_by_volume


def _format_minutes_ago_input(minutes_ago: str | None) -> str | None:
    if not minutes_ago:
        return None
    minutes_ago = minutes_ago.strip()
    if not minutes_ago or _alias_key(minutes_ago) == _alias_key(NOW_TIME_CHOICE):
        return None
    try:
        _parse_time_offset_parts(minutes_ago)
    except typer.BadParameter:
        pass
    else:
        return minutes_ago
    amount = _parse_decimal_amount(minutes_ago)
    unit = "minute" if amount == 1 else "minutes"
    return f"{_format_decimal(amount)} {unit} ago"


def _format_note_choice(note: str | None) -> str | None:
    if note is None or _alias_key(note) == _alias_key(EMPTY_NOTE_CHOICE):
        return None
    return note


def _menu_for_extra_info(
    executable: str,
    note: str | None,
    time: str | None,
    allow_dosage_by_volume: bool = False,
    allow_back: bool = False,
) -> tuple[str | None, str | None, bool]:
    while True:
        add_extra_info = _menu_for_value(
            executable,
            EXTRA_INFO_PROMPT,
            EXTRA_INFO_SKIP_CHOICE,
            candidates=[EXTRA_INFO_SKIP_CHOICE, EXTRA_INFO_SHOW_CHOICE],
            aliases=EXTRA_INFO_CHOICE_ALIASES,
            optional=True,
            allow_back=allow_back,
        )
        if add_extra_info is None:
            return note, time, False
        if add_extra_info == EXTRA_INFO_SKIP_CHOICE:
            return note, time, False
        if add_extra_info == EXTRA_INFO_SHOW_CHOICE:
            while True:
                try:
                    selected_note = _menu_for_value(
                        executable,
                        "Note",
                        note or EMPTY_NOTE_CHOICE,
                        candidates=[
                            *([note] if note else []),
                            EMPTY_NOTE_CHOICE,
                        ],
                        optional=True,
                        allow_back=allow_back,
                    )
                    selected_time = _menu_for_time_details(executable, time, allow_back=allow_back)
                    dosage_by_volume = "No"
                    if allow_dosage_by_volume:
                        dosage_by_volume = _menu_for_value(
                            executable,
                            "Dosage by volume?",
                            "No",
                            candidates=["No", "Yes"],
                            aliases=EXTRA_INFO_ALIASES,
                            optional=True,
                            allow_back=allow_back,
                        )
                except BackRequested:
                    continue
                return _format_note_choice(selected_note), selected_time, dosage_by_volume == "Yes"


def _prompt_for_time_details(time: str | None) -> str | None:
    minutes_ago = _prompt_for_value("Minutes ago", NOW_TIME_CHOICE, optional=True)
    if minutes_ago and _alias_key(minutes_ago) == _alias_key(NOW_TIME_CHOICE):
        return time
    formatted_minutes_ago = _format_minutes_ago_input(minutes_ago)
    if formatted_minutes_ago:
        return formatted_minutes_ago
    exact_time = _prompt_for_value("Exact time", time, optional=True)
    return exact_time or time


def _menu_for_time_details(
    executable: str,
    time: str | None,
    allow_back: bool = False,
) -> str | None:
    while True:
        minutes_ago = _menu_for_value(
            executable,
            "Minutes ago",
            NOW_TIME_CHOICE,
            candidates=[NOW_TIME_CHOICE, *TIME_CHOICES],
            optional=True,
            allow_back=allow_back,
        )
        if minutes_ago and _alias_key(minutes_ago) == _alias_key(NOW_TIME_CHOICE):
            return time
        formatted_minutes_ago = _format_minutes_ago_input(minutes_ago)
        if formatted_minutes_ago:
            return formatted_minutes_ago
        try:
            exact_time = _menu_for_value(
                executable,
                "Exact time",
                time,
                optional=True,
                allow_back=allow_back,
            )
        except BackRequested:
            continue
        return exact_time or time


def _is_intravenous_route(roa: str) -> bool:
    return (_resolve_alias(roa, ROA_ALIASES) or roa) == "intravenous"


def _route_uses_needle(roa: str) -> bool:
    return _is_injection_route(_resolve_alias(roa, ROA_ALIASES) or roa)


def _split_mixture_values(value: str | None) -> list[str]:
    if not value:
        return []
    value = value.strip()
    list_values = _split_bracketed_list_value(value)
    if list_values:
        return list_values
    value = _strip_wrapping_angle_list(value)
    if ";" in value:
        delimiter = ";"
    elif "/" in value:
        delimiter = "/"
    else:
        delimiter = "+"
    return [part.strip() for part in value.split(delimiter) if part.strip()]


def _strip_wrapping_angle_list(value: str) -> str:
    if value.startswith("<") and value.endswith(">"):
        return value[1:-1].strip()
    if value.startswith("\\<") and value.endswith("\\>"):
        return value[2:-2].strip()
    return value


def _split_bracketed_list_value(value: str | None) -> list[str]:
    if not value:
        return []
    value = value.strip()
    end_index = _bracketed_list_end_index(value)
    if end_index is None:
        return []
    list_value = value[1:end_index]
    try:
        parts = next(csv.reader([list_value], skipinitialspace=True))
    except csv.Error:
        parts = list_value.split(",")
    return [part.strip() for part in parts if part.strip()]


def _split_mixture_values_for_count(value: str | None, count: int) -> list[str]:
    values = _split_mixture_values(value)
    if count <= 0 or len(values) <= count:
        return values

    stripped = (value or "").strip()
    if _bracketed_list_end_index(stripped) is None:
        return values

    overflow = len(values) - count + 1
    return [",".join(values[:overflow]), *values[overflow:]]


def _bracketed_list_end_index(value: str) -> int | None:
    if not value.startswith("["):
        return None
    in_quotes = False
    index = 1
    while index < len(value):
        char = value[index]
        if char == '"':
            if in_quotes and index + 1 < len(value) and value[index + 1] == '"':
                index += 2
                continue
            in_quotes = not in_quotes
        elif char == "]" and not in_quotes:
            return index
        index += 1
    return None


def _dosage_without_volume(dosage: str) -> str:
    dosage = dosage.strip()
    list_end_index = _bracketed_list_end_index(dosage)
    if list_end_index is not None:
        return dosage[:list_end_index + 1]
    if "@" not in dosage:
        return dosage
    before, after = [part.strip() for part in dosage.split("@", 1)]
    return after if before.endswith("ml") and after else before


def _volume_from_dosage(dosage: str) -> str | None:
    if "@" not in dosage:
        return None
    before, after = [_clean_volume_choice(part) for part in dosage.rsplit("@", 1)]
    if _is_volume_value(after):
        return after
    if _is_volume_value(before):
        return before
    return None


def _clean_volume_choice(value: str) -> str:
    return value.strip().strip('"').strip("'").strip()


def _is_volume_value(value: str) -> bool:
    match = VOLUME_RE.match(value)
    if not match:
        return False
    return match.group("unit").casefold() in VOLUME_UNIT_TO_ML


DOSAGE_AMOUNT_RE = re.compile(
    r"^\s*(?P<guess>~?)\s*"
    r"(?P<amount>[+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    r"(?:\s*-\s*(?P<range_amount>[+-]?(?:\d+(?:\.\d*)?|\.\d+)))?"
    r"\s*(?P<unit>[a-zA-Z\u00b5\u03bc]+)\b"
)


def _parse_daily_usage_date(value: str) -> date:
    if value == DAILY_USAGE_TODAY:
        return datetime.now(timezone.utc).date()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise typer.BadParameter("Daily usage date must use d.m.y format, e.g. 23.9.2026")


def _parse_log_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_log_timestamp_date(value: str) -> date | None:
    parsed = _parse_log_timestamp(value)
    return parsed.date() if parsed else None


def _normalize_usage_unit(amount: Decimal, unit: str) -> tuple[Decimal, str] | None:
    unit_key = unit.casefold().replace("\u00b5", "u").replace("\u03bc", "u")
    if unit_key == "g":
        return amount * Decimal("1000"), "mg"
    if unit_key == "mg":
        return amount, "mg"
    if unit_key in {"ug", "mcg"}:
        return amount, "ug"
    if unit_key == "ml":
        return amount, "ml"
    return None


def _parse_usage_dosage(dosage: str) -> tuple[Decimal, str] | None:
    match = DOSAGE_AMOUNT_RE.match(dosage)
    if not match:
        return None
    if match.group("range_amount"):
        return None
    try:
        amount = Decimal(match.group("amount"))
    except InvalidOperation:
        return None
    return _normalize_usage_unit(amount, match.group("unit"))


def _collect_daily_usage(logfile: Path, usage_date: date) -> tuple[dict[tuple[str, str], Decimal], int, int]:
    totals: dict[tuple[str, str], Decimal] = {}
    counted_rows = 0
    skipped_dosages = 0
    if not logfile.exists():
        return totals, counted_rows, skipped_dosages

    with open(logfile, newline="") as infile:
        for row in csv.reader(infile):
            if len(row) <= 3 or row[0].strip().lower() == "timestamp":
                continue
            if _parse_log_timestamp_date(row[0]) != usage_date:
                continue

            counted_rows += 1
            dosages = _split_mixture_values(_dosage_without_volume(row[3]))
            substances = _split_mixture_values_for_count(row[2], len(dosages))
            for substance, dosage in zip(substances, dosages):
                parsed_dosage = _parse_usage_dosage(dosage)
                if parsed_dosage is None:
                    skipped_dosages += 1
                    continue
                amount, unit = parsed_dosage
                key = (substance, unit)
                totals[key] = totals.get(key, Decimal("0")) + amount

    return totals, counted_rows, skipped_dosages


def _format_daily_usage_date(value: date) -> str:
    return f"{value.day}.{value.month}.{value.year}"


def _print_daily_usage(logfile: Path, value: str) -> None:
    usage_date = _parse_daily_usage_date(value)
    totals, counted_rows, skipped_dosages = _collect_daily_usage(logfile, usage_date)
    con.print(f"Daily usage for {_format_daily_usage_date(usage_date)}:")
    if not totals:
        con.print("No usage found.")
    else:
        for (substance, unit), amount in sorted(
            totals.items(),
            key=lambda item: (item[0][0].casefold(), item[0][1]),
        ):
            con.print(f"{substance}: {_format_decimal(amount)}{unit}")
    con.print(f"Rows counted: {counted_rows}")
    if skipped_dosages:
        con.print(f"Skipped dosages: {skipped_dosages}", style="yellow")


def _stats_period_key(row_date: date, timescale: str) -> str:
    if timescale == STATISTICS_TIMESCALE_DAILY:
        return row_date.isoformat()
    if timescale == STATISTICS_TIMESCALE_WEEKLY:
        year, week, _ = row_date.isocalendar()
        return f"{year}-W{week:02d}"
    if timescale == STATISTICS_TIMESCALE_MONTHLY:
        return f"{row_date.year}-{row_date.month:02d}"
    return STATISTICS_TIMESCALE_TOTAL


def _stats_period_sort_key(period_key: str, timescale: str) -> tuple[int, ...]:
    if timescale == STATISTICS_TIMESCALE_DAILY:
        parsed = date.fromisoformat(period_key)
        return (parsed.year, parsed.month, parsed.day)
    if timescale == STATISTICS_TIMESCALE_WEEKLY:
        year, week = period_key.split("-W", 1)
        return (int(year), int(week))
    if timescale == STATISTICS_TIMESCALE_MONTHLY:
        year, month = period_key.split("-", 1)
        return (int(year), int(month))
    return (0,)


def _empty_stats_period() -> dict[str, object]:
    return {
        "ingestions": 0,
        "substances": {},
        "routes": {},
    }


def _format_total_amount(amounts: dict[str, Decimal]) -> str:
    parts = []
    grams = Decimal("0")
    for unit, amount in amounts.items():
        if unit == "mg":
            grams += amount / Decimal("1000")
        elif unit == "ug":
            grams += amount / Decimal("1000000")
        elif unit == "g":
            grams += amount
        else:
            parts.append(f"{_format_decimal(amount)}{unit}")
    if grams:
        parts.insert(0, f"{_format_decimal(grams)}g")
    return ", ".join(parts) if parts else "unknown"


def _collect_statistics(logfile: Path, timescale: str) -> dict[str, dict[str, object]]:
    periods: dict[str, dict[str, object]] = {}
    if not logfile.exists():
        return periods

    with open(logfile, newline="") as infile:
        for row in csv.reader(infile):
            if len(row) <= 4 or row[0].strip().lower() == "timestamp":
                continue
            row_date = _parse_log_timestamp_date(row[0])
            if row_date is None:
                continue
            period_key = _stats_period_key(row_date, timescale)
            period = periods.setdefault(period_key, _empty_stats_period())
            period["ingestions"] = int(period["ingestions"]) + 1

            route = _format_route_for_log(row[4]) or row[4].strip().casefold()
            routes = period["routes"]
            if isinstance(routes, dict) and route:
                routes[route] = int(routes.get(route, 0)) + 1

            dosage_value = _dosage_without_volume(row[3]) if len(row) > 3 else ""
            dosages = _split_mixture_values(dosage_value)
            substances = _split_mixture_values_for_count(row[2], len(dosages))
            substances_by_index = substances if isinstance(substances, list) else _split_mixture_values(substances)
            substance_stats = period["substances"]
            if not isinstance(substance_stats, dict):
                continue
            for substance, dosage in zip(substances_by_index, dosages):
                name = _resolve_alias(substance, SUBSTANCE_ALIASES) or substance
                entry = substance_stats.setdefault(name, {"ingestions": 0, "amounts": {}})
                entry["ingestions"] = int(entry["ingestions"]) + 1
                parsed_dosage = _parse_usage_dosage(dosage)
                if parsed_dosage is None:
                    continue
                amount, unit = parsed_dosage
                amounts = entry["amounts"]
                if isinstance(amounts, dict):
                    amounts[unit] = amounts.get(unit, Decimal("0")) + amount
    return periods


def _period_choices(periods: dict[str, dict[str, object]], timescale: str) -> tuple[list[str], dict[str, str]]:
    keys = sorted(periods, key=lambda key: _stats_period_sort_key(key, timescale), reverse=True)
    labels = {
        key: f"{key} {{{int(periods[key]['ingestions'])}}}"
        for key in keys
    }
    return keys, labels


def _statistics_substance_lines(period: dict[str, object]) -> list[str]:
    substances = period.get("substances")
    if not isinstance(substances, dict):
        return []
    rows = []
    for substance, value in substances.items():
        if not isinstance(value, dict):
            continue
        ingestions = int(value.get("ingestions", 0))
        amounts = value.get("amounts")
        total = _format_total_amount(amounts if isinstance(amounts, dict) else {})
        rows.append((ingestions, substance, f"{substance} (total: {total}, ingestions: {ingestions})"))
    return [row[2] for row in sorted(rows, key=lambda row: (-row[0], row[1].casefold()))]


def _statistics_route_lines(period: dict[str, object]) -> list[str]:
    routes = period.get("routes")
    if not isinstance(routes, dict):
        return []
    rows = [
        (int(count), route, f"{route} ({int(count)})")
        for route, count in routes.items()
    ]
    return [row[2] for row in sorted(rows, key=lambda row: (-row[0], row[1].casefold()))]


def _show_statistics_lines(mode: InputMode, title: str, lines: list[str]) -> None:
    if not lines:
        con.print("No statistics found.")
        return
    if mode == InputMode.prompt:
        con.print(f"{title}:")
        for line in lines:
            con.print(f"- {line}")
        return
    _menu_for_value(
        mode.value,
        title,
        lines[0],
        candidates=[*lines, BACK_CHOICE],
        optional=True,
    )


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


def _show_statistics(mode: InputMode, logfile: Path) -> None:
    while True:
        timescale = _select_statistics_choice(
            mode,
            "Statistics",
            [
                STATISTICS_TIMESCALE_DAILY,
                STATISTICS_TIMESCALE_WEEKLY,
                STATISTICS_TIMESCALE_MONTHLY,
                STATISTICS_TIMESCALE_TOTAL,
            ],
        )
        if timescale == BACK_CHOICE:
            return
        periods = _collect_statistics(logfile, timescale)
        period_keys, period_labels = _period_choices(periods, timescale)
        if not period_keys:
            con.print("No statistics found.")
            continue
        period_key = _select_statistics_choice(mode, timescale, period_keys, period_labels)
        if period_key == BACK_CHOICE:
            continue
        section = _select_statistics_choice(
            mode,
            period_labels.get(period_key, period_key),
            [STATISTICS_SECTION_SUBSTANCES, STATISTICS_SECTION_ROUTES],
        )
        if section == BACK_CHOICE:
            continue
        period = periods[period_key]
        if section == STATISTICS_SECTION_SUBSTANCES:
            _show_statistics_lines(mode, section, _statistics_substance_lines(period))
        elif section == STATISTICS_SECTION_ROUTES:
            _show_statistics_lines(mode, section, _statistics_route_lines(period))


def _load_webhook_message_cache() -> dict[str, dict[str, dict[str, object]]]:
    if not WEBHOOK_MESSAGE_CACHE_FILE.exists():
        return {}
    try:
        with open(WEBHOOK_MESSAGE_CACHE_FILE) as infile:
            raw = json.load(infile)
    except (OSError, json.JSONDecodeError):
        return {}
    logs = raw.get("logs") if isinstance(raw, dict) else None
    if not isinstance(logs, dict):
        return {}
    return {
        log_key: {ts: record for ts, record in entries.items() if isinstance(record, dict) and record.get("id")}
        for log_key, entries in logs.items()
        if isinstance(entries, dict)
    }


def _write_webhook_message_cache(cache: dict[str, dict[str, dict[str, object]]]) -> None:
    WEBHOOK_MESSAGE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": WEBHOOK_MESSAGE_CACHE_VERSION, "logs": {key: value for key, value in cache.items() if value}}
    tmpfile = WEBHOOK_MESSAGE_CACHE_FILE.with_suffix(f"{WEBHOOK_MESSAGE_CACHE_FILE.suffix}.tmp")
    with open(tmpfile, "w") as outfile:
        json.dump(payload, outfile, indent=2, sort_keys=True)
        outfile.write("\n")
    tmpfile.replace(WEBHOOK_MESSAGE_CACHE_FILE)


def _webhook_id(webhook_url: str) -> str | None:
    match = re.search(r"/webhooks/(?P<id>\d+)/", webhook_url)
    return match.group("id") if match else None


def _store_webhook_message(logfile: Path, timestamp: str, message_id: str, webhook_url: str, backdated: bool) -> None:
    cache = _load_webhook_message_cache()
    cache.setdefault(_logfile_cache_key(logfile), {})[timestamp] = {
        "id": message_id,
        "webhook_id": _webhook_id(webhook_url),
        "backdated": backdated,
    }
    _write_webhook_message_cache(cache)


def _get_webhook_message(logfile: Path, timestamp: str) -> dict[str, object] | None:
    return _load_webhook_message_cache().get(_logfile_cache_key(logfile), {}).get(timestamp)


def _pop_webhook_message(logfile: Path, timestamp: str) -> dict[str, object] | None:
    cache = _load_webhook_message_cache()
    record = cache.get(_logfile_cache_key(logfile), {}).pop(timestamp, None)
    if record is not None:
        _write_webhook_message_cache(cache)
    return record


def _move_webhook_message(logfile: Path, old_timestamp: str, new_timestamp: str) -> None:
    if old_timestamp == new_timestamp:
        return
    cache = _load_webhook_message_cache()
    entries = cache.get(_logfile_cache_key(logfile), {})
    record = entries.pop(old_timestamp, None)
    if record is None:
        return
    entries[new_timestamp] = record
    _write_webhook_message_cache(cache)


def _webhook_message_url(webhook_url: str, message_id: str) -> str:
    return f"{webhook_url.rstrip('/')}/messages/{message_id}"


def _post_webhook_message(webhook_url: str, logline: str) -> str | None:
    response = requests.post(webhook_url, params={"wait": "true"}, json={"content": logline, "flags": 4})
    response.raise_for_status()
    con.print(f"Status: {response.status_code}")
    try:
        message_id = response.json().get("id")
    except ValueError:
        return None
    return str(message_id) if message_id else None


def _edit_webhook_message(webhook_url: str, message_id: str, logline: str) -> None:
    response = requests.patch(_webhook_message_url(webhook_url, message_id), json={"content": logline, "flags": 4})
    response.raise_for_status()


def _delete_webhook_message(webhook_url: str, message_id: str) -> None:
    response = requests.delete(_webhook_message_url(webhook_url, message_id))
    response.raise_for_status()


def _format_logline(
    user: str,
    userpage: str | None,
    kind: LogKind,
    dosage: str,
    substance_md: str,
    salt: str | None,
    roa: str,
    logged_site: str | None,
    note: str | None,
    time_now: str,
    is_backdated: bool,
) -> str:
    user_md = f"[{user}]({userpage})" if userpage else user
    if kind == LogKind.mixture:
        logline = f"{user_md}: {dosage} {substance_md}" + (
            f" {salt.replace('<','[').replace('>',']')}" if salt else ""
        )
    else:
        logline = f"{user_md}: {dosage} {substance_md}" + (
            f" [{salt}]" if salt else ""
        )
    webhook_site = _format_webhook_site(roa, logged_site)
    return logline + f" via {roa}" + (
        f" at {webhook_site} site" if webhook_site else ""
    ) + (
        f"\n> {note}" if note else ""
    ) + (
        f"\n> {time_now}" if is_backdated else ""
    )


def _cached_substance_md(title: str) -> str:
    """Link substances from the local substance cache only (no lookups)."""
    components = _split_mixture_values(title) or [title]
    titles = []
    linked = []
    for component in components:
        cached = _resolve_cached_substance_title(component)
        titles.append(cached or component)
        linked.append(_format_substance_link(cached) if cached else component)
    if len(components) <= 1:
        return linked[0]
    return _format_linked_substance_mixture(titles, linked)


def _logline_from_row(row: list[str], userpage: str | None, backdated: bool) -> str:
    timestamp, user, title, dosage, roa, site, salt, note = _padded_log_row(row)[:len(LOG_COLUMNS)]
    if _is_mixture_value(title):
        kind = LogKind.mixture
    elif "@" in dosage:
        kind = LogKind.solution
    else:
        kind = LogKind.single
    return _format_logline(user, userpage, kind, dosage, _cached_substance_md(title), salt or None, roa, site or None, note or None, timestamp, backdated)


def _webhook_record_usable(record: dict[str, object] | None, webhook: HttpUrl | None) -> str | None:
    """Return the webhook URL to use for a stored message, or None with a reason printed."""
    if not record:
        return None
    if not webhook:
        err_con.print("Webhook message not updated: no webhook configured", style="bold yellow")
        return None
    webhook_url = webhook.encoded_string()
    stored_id = record.get("webhook_id")
    if stored_id and stored_id != _webhook_id(webhook_url):
        err_con.print("Webhook message not updated: it was posted through a different webhook", style="bold yellow")
        return None
    return webhook_url


def _delete_history_webhook_message(logfile: Path, timestamp: str, webhook: HttpUrl | None) -> None:
    record = _pop_webhook_message(logfile, timestamp)
    webhook_url = _webhook_record_usable(record, webhook)
    if not webhook_url:
        return
    try:
        _delete_webhook_message(webhook_url, str(record["id"]))
        con.print("Deleted webhook message.")
    except Exception as exc:
        err_con.print(f"Webhook delete failed: '{exc}'", style="bold yellow")


def _update_history_webhook_message(logfile: Path, row: list[str], webhook: HttpUrl | None, userpage: str | None) -> None:
    timestamp = _padded_log_row(row)[0]
    record = _get_webhook_message(logfile, timestamp)
    webhook_url = _webhook_record_usable(record, webhook)
    if not webhook_url:
        return
    try:
        _edit_webhook_message(webhook_url, str(record["id"]), _logline_from_row(row, userpage, bool(record.get("backdated"))))
        con.print("Updated webhook message.")
    except Exception as exc:
        err_con.print(f"Webhook edit failed: '{exc}'", style="bold yellow")


def _load_userpage_json() -> object:
    if not USERPAGE_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(USERPAGE_CACHE_FILE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"Could not read {USERPAGE_CACHE_FILE}: {exc}") from exc


def _write_userpage_json(data: object) -> None:
    USERPAGE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmpfile = USERPAGE_CACHE_FILE.with_suffix(f"{USERPAGE_CACHE_FILE.suffix}.tmp")
    with open(tmpfile, "w") as outfile:
        json.dump(data, outfile, indent=2, ensure_ascii=False)
        outfile.write("\n")
    tmpfile.replace(USERPAGE_CACHE_FILE)


def _json_summary(value: object) -> str:
    if isinstance(value, dict):
        name = value.get(JSON_NAME_KEY)
        if isinstance(name, str) and name.strip():
            return name
        return f"{{{len(value)} keys}}"
    if isinstance(value, list):
        return f"[{len(value)} items]"
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 60 else f"{text[:57]}..."


def _json_value_text(value: object) -> str:
    """Current value as shown for editing: strings raw, everything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _parse_json_input(text: str) -> object:
    """Typed input is JSON when it parses (3, true, null, "x", [..], {..}); otherwise a string."""
    try:
        return json.loads(text.strip())
    except ValueError:
        return text


def _prompt_json_text(mode: InputMode, label: str, current: str | None) -> str | None:
    if mode == InputMode.prompt:
        return _prompt_for_value(label, current, optional=True)
    return _menu_for_value(
        mode.value,
        label,
        current,
        candidates=[current] if current else [],
        optional=True,
        keep_custom_prefix=True,
    )


def _json_container_at(root: object, path: list[object]) -> object:
    node = root
    for key in path:
        node = node[key]
    return node


def _json_entry_choices(container: dict | list) -> tuple[list[str], dict[str, str], list[object]]:
    if isinstance(container, dict):
        keys: list[object] = list(container.keys())
        labels = {f"{JSON_ENTRY_PREFIX}{index}": f"{key}: {_json_summary(container[key])}" for index, key in enumerate(keys)}
    else:
        keys = list(range(len(container)))
        labels = {f"{JSON_ENTRY_PREFIX}{index}": f"{index}: {_json_summary(item)}" for index, item in enumerate(container)}
    return list(labels), labels, keys


def _userpage_schema(path: list[object]) -> dict[str, object]:
    """Known annotation fields for the container at `path` inside userpage.json.

    "fields" maps a dict's expected keys to "list" or "str"; "item" names the
    shape of new items in a list ("str" or "route")."""
    pattern = tuple("*" if isinstance(key, int) else key for key in path)
    if pattern == (USERPAGE_SUBSTANCES_KEY, "*"):
        return {"fields": {USERPAGE_ROUTES_KEY: "list", **{field: "list" for field in USERPAGE_SUBSTANCE_LIST_FIELDS}}}
    if pattern == (USERPAGE_SUBSTANCES_KEY, "*", USERPAGE_ROUTES_KEY):
        return {"item": "route"}
    if pattern == (USERPAGE_SUBSTANCES_KEY, "*", USERPAGE_ROUTES_KEY, "*"):
        return {"fields": {field: "str" for field in USERPAGE_ROUTE_FIELDS}}
    if len(pattern) == 3 and pattern[:2] == (USERPAGE_SUBSTANCES_KEY, "*") and pattern[2] in USERPAGE_SUBSTANCE_LIST_FIELDS:
        return {"item": "str"}
    return {}


def _edit_json_container(mode: InputMode, root: object, path: list[object]) -> None:
    while True:
        container = _json_container_at(root, path)
        if not isinstance(container, (dict, list)):
            return
        title = " / ".join([ACTION_USERPAGE, *(str(key) for key in path)])
        schema = _userpage_schema(path)
        entry_ids, labels_by_value, keys = _json_entry_choices(container)
        missing_fields = []
        if isinstance(container, dict):
            missing_fields = [field for field in schema.get("fields", {}) if field not in container]
            for index, field in enumerate(missing_fields):
                placeholder = "[]" if schema["fields"][field] == "list" else '""'
                labels_by_value[f"{JSON_MISSING_PREFIX}{index}"] = f"{field}: {placeholder} (missing)"
        actions = [USERPAGE_ADD_KEY_CHOICE if isinstance(container, dict) else USERPAGE_ADD_ITEM_CHOICE]
        if keys:
            actions.append(USERPAGE_REMOVE_CHOICE)
        labels_by_value.update({action: action for action in actions})
        choices = [*entry_ids, *(f"{JSON_MISSING_PREFIX}{index}" for index in range(len(missing_fields))), *actions]
        selected = _select_statistics_choice(mode, title, choices, labels_by_value, back_choice=USERPAGE_CLOSE_CHOICE)
        if selected == USERPAGE_CLOSE_CHOICE:
            return
        if selected == USERPAGE_ADD_KEY_CHOICE:
            key = (_prompt_json_text(mode, "Key", None) or "").strip()
            if not key:
                continue
            if key in container:
                err_con.print(f"Key '{key}' already exists", style="bold yellow")
                continue
            text = _prompt_json_text(mode, key, None)
            if text is None:
                continue
            container[key] = _parse_json_input(text)
            _write_userpage_json(root)
            con.print(f"Added {key}.")
            continue
        if selected == USERPAGE_ADD_ITEM_CHOICE:
            item_shape = schema.get("item")
            if item_shape == "route":
                name = _prompt_json_text(mode, "Route name", None)
                if name is None or not name.strip():
                    continue
                container.append({JSON_NAME_KEY: name.strip()})
                _write_userpage_json(root)
                con.print(f"Added route {name.strip()}.")
                _edit_json_container(mode, root, [*path, len(container) - 1])
                continue
            text = _prompt_json_text(mode, f"Item {len(container)}", None)
            if text is None:
                continue
            container.append(text if item_shape == "str" else _parse_json_input(text))
            _write_userpage_json(root)
            con.print("Added item.")
            continue
        if selected == USERPAGE_REMOVE_CHOICE:
            target = _select_statistics_choice(
                mode, f"{title} / {USERPAGE_REMOVE_CHOICE}", entry_ids, labels_by_value, back_choice=USERPAGE_CLOSE_CHOICE
            )
            if target == USERPAGE_CLOSE_CHOICE or not target.startswith(JSON_ENTRY_PREFIX):
                continue
            key = keys[int(target[len(JSON_ENTRY_PREFIX):])]
            del container[key]
            _write_userpage_json(root)
            con.print(f"Removed {key}.")
            continue
        if selected.startswith(JSON_MISSING_PREFIX):
            field = missing_fields[int(selected[len(JSON_MISSING_PREFIX):])]
            if schema["fields"][field] == "list":
                container[field] = []
                _write_userpage_json(root)
                _edit_json_container(mode, root, [*path, field])
                continue
            text = _prompt_json_text(mode, field, None)
            if text is None:
                continue
            container[field] = text
            _write_userpage_json(root)
            con.print(f"Added {field}.")
            continue
        if selected.startswith(JSON_ENTRY_PREFIX):
            key = keys[int(selected[len(JSON_ENTRY_PREFIX):])]
            value = container[key]
            if isinstance(value, (dict, list)):
                _edit_json_container(mode, root, [*path, key])
                continue
            text = _prompt_json_text(mode, str(key), _json_value_text(value))
            if text is None:
                continue
            field_shape = schema.get("fields", {}).get(key) if isinstance(container, dict) else schema.get("item")
            container[key] = text if field_shape == "str" else _parse_json_input(text)
            _write_userpage_json(root)
            con.print(f"Updated {key}.")
            continue
        raise typer.BadParameter(f"Unknown userpage choice: {selected}")


def _userpage_menu(mode: InputMode, userpage: str | None) -> None:
    while True:
        choice = _select_statistics_choice(
            mode,
            ACTION_USERPAGE,
            [USERPAGE_ANNOTATE_CHOICE, USERPAGE_MANUAL_CHOICE, USERPAGE_RESET_CHOICE, ],
        )
        if choice == BACK_CHOICE:
            return
        if choice == USERPAGE_RESET_CHOICE:
            _download_userpage(userpage)
            continue
        if choice == USERPAGE_ANNOTATE_CHOICE:
            con.print("Add annotations is not available yet.")
            continue
        if choice == USERPAGE_MANUAL_CHOICE:
            _edit_userpage(mode)
            continue
        raise typer.BadParameter(f"Unknown userpage choice: {choice}")


def _edit_userpage(mode: InputMode) -> None:
    root = _load_userpage_json()
    if not isinstance(root, (dict, list)):
        raise typer.BadParameter(f"{USERPAGE_CACHE_FILE} does not hold a JSON object or array")
    if not USERPAGE_CACHE_FILE.exists():
        con.print(f"{USERPAGE_CACHE_FILE} does not exist yet; it is created on the first change.")
    _edit_json_container(mode, root, [])


def _read_log_rows(logfile: Path) -> list[list[str]]:
    if not logfile.exists():
        return []
    with open(logfile, newline="") as infile:
        return list(csv.reader(infile))


def _is_log_header_row(row: list[str]) -> bool:
    return bool(row) and row[0].strip().lower() == "timestamp"


def _write_log_rows(logfile: Path, rows: list[list[str]]) -> None:
    tmpfile = logfile.with_suffix(f"{logfile.suffix}.tmp")
    with open(tmpfile, "w", newline="") as outfile:
        csv.writer(outfile).writerows(rows)
    tmpfile.replace(logfile)
    # Rebuild the cached menu choices from the remaining rows (a local read of
    # the CSV, no substance lookups), so choices that no longer appear in any
    # row are dropped right away.
    _sync_choice_cache_from_history(logfile)


def _padded_log_row(row: list[str]) -> list[str]:
    return [*row, *([""] * (len(LOG_COLUMNS) - len(row)))]


def _history_entry_label(row: list[str]) -> str:
    padded = _padded_log_row(row)
    timestamp = _parse_log_timestamp(padded[0])
    when = timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else padded[0]
    parts = [when, padded[3], padded[2]]
    if padded[4]:
        parts.append(f"via {padded[4]}")
    if padded[5]:
        parts.append(f"at {padded[5]}")
    if padded[6]:
        parts.append(f"[{padded[6]}]")
    return " ".join(part for part in parts if part)


def _normalize_history_value(column: str, value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if column == "timestamp":
        parsed = _parse_log_timestamp(value)
        if parsed is None:
            raise typer.BadParameter(f"Could not parse '{value}' as a timestamp")
        return parsed.isoformat(timespec="milliseconds")
    if column == "substance":
        return _resolve_alias(value, SUBSTANCE_ALIASES) or value
    if column == "dosage":
        return _format_dosage_expression(value)
    if column == "roa":
        return _format_route_for_log(value) or value
    if column == "site":
        return _logged_site(value) or ""
    if column == "salt":
        return _format_salt_for_log(_resolve_alias(value, SALT_ALIASES)) or ""
    return value


def _edit_history_fields(mode: InputMode, logfile: Path, row_index: int, webhook: HttpUrl | None, userpage: str | None) -> None:
    while True:
        rows = _read_log_rows(logfile)
        if row_index >= len(rows):
            return
        row = _padded_log_row(rows[row_index])
        labels_by_value = {
            column: f"{column}: {value}" if value else column
            for column, value in zip(LOG_COLUMNS, row)
        }
        selected = _select_statistics_choice(mode, "Edit log", LOG_COLUMNS, labels_by_value)
        if selected == BACK_CHOICE:
            return
        if selected not in LOG_COLUMNS:
            raise typer.BadParameter(f"Unknown log field: {selected}")
        column_index = LOG_COLUMNS.index(selected)
        current = row[column_index]
        if mode == InputMode.prompt:
            new_value = _prompt_for_value(selected, current, optional=True)
        else:
            new_value = _menu_for_value(
                mode.value,
                selected,
                current,
                candidates=[current] if current else [],
                optional=True,
                keep_custom_prefix=selected == "dosage",
            )
        row[column_index] = _normalize_history_value(selected, new_value or "")
        rows[row_index] = row
        _write_log_rows(logfile, rows)
        con.print(f"Updated {selected}.")
        if selected == "timestamp":
            _move_webhook_message(logfile, current, row[column_index])
        _update_history_webhook_message(logfile, row, webhook, userpage)


def _edit_history_entry(mode: InputMode, logfile: Path, row_index: int, webhook: HttpUrl | None, userpage: str | None) -> None:
    while True:
        rows = _read_log_rows(logfile)
        if row_index >= len(rows) or _is_log_header_row(rows[row_index]):
            return
        action = _select_statistics_choice(
            mode,
            _history_entry_label(rows[row_index]),
            [HISTORY_EDIT_CHOICE, HISTORY_REMOVE_CHOICE],
        )
        if action == BACK_CHOICE:
            return
        if action == HISTORY_REMOVE_CHOICE:
            removed = rows.pop(row_index)
            _write_log_rows(logfile, rows)
            con.print("Removed log entry.")
            _delete_history_webhook_message(logfile, _padded_log_row(removed)[0], webhook)
            return
        if action == HISTORY_EDIT_CHOICE:
            _edit_history_fields(mode, logfile, row_index, webhook, userpage)
            continue
        raise typer.BadParameter(f"Unknown history action: {action}")


def _prompt_history_page(mode: InputMode, page_number: int, page_count: int) -> int | None:
    """Ask for a history page number; None when cancelled or invalid."""
    label = f"Page (1-{page_count})"
    if mode == InputMode.prompt:
        text = _prompt_for_value(label, str(page_number), optional=True)
    else:
        text = _menu_for_value(
            mode.value,
            label,
            str(page_number),
            candidates=[str(number) for number in range(1, page_count + 1)],
            optional=True,
        )
    if text is None:
        return None
    try:
        target = int(text.strip())
    except ValueError:
        err_con.print(f"'{text}' is not a page number", style="bold yellow")
        return None
    if target < 1 or target > page_count:
        err_con.print(f"Page {target} is out of range (1-{page_count})", style="bold yellow")
        return None
    return target


def _show_history(mode: InputMode, logfile: Path, webhook: HttpUrl | None, userpage: str | None) -> None:
    offset = 0
    while True:
        rows = _read_log_rows(logfile)
        entries = [(index, row) for index, row in enumerate(rows) if row and not _is_log_header_row(row)]
        entries.reverse()
        page = entries[offset:offset + HISTORY_PAGE_SIZE]
        if not page and offset:
            offset = max(0, offset - HISTORY_PAGE_SIZE)
            continue
        if not page:
            con.print("No logs found.")
            return
        choices = [str(index) for index, _ in page]
        labels_by_value = {str(index): _history_entry_label(row) for index, row in page}
        page_number = offset // HISTORY_PAGE_SIZE + 1
        page_count = (len(entries) + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE
        if offset + HISTORY_PAGE_SIZE < len(entries):
            choices.append(HISTORY_NEXT_CHOICE)
            labels_by_value[HISTORY_NEXT_CHOICE] = HISTORY_NEXT_CHOICE
        if page_count > 1:
            choices.append(HISTORY_GOTO_CHOICE)
            labels_by_value[HISTORY_GOTO_CHOICE] = HISTORY_GOTO_CHOICE
        selected = _select_statistics_choice(mode, f"History ({page_number} of {page_count})", choices, labels_by_value)
        if selected == BACK_CHOICE:
            return
        if selected == HISTORY_NEXT_CHOICE:
            offset += HISTORY_PAGE_SIZE
            continue
        if selected == HISTORY_GOTO_CHOICE:
            target = _prompt_history_page(mode, page_number, page_count)
            if target is not None:
                offset = (target - 1) * HISTORY_PAGE_SIZE
            continue
        if selected not in labels_by_value:
            raise typer.BadParameter(f"Unknown log entry: {selected}")
        _edit_history_entry(mode, logfile, int(selected), webhook, userpage)


def _format_list_value(values: list[str | None]) -> str | None:
    present = [value for value in values if value]
    if not present:
        return None
    return '/'.join(_format_list_item(value) for value in present)


def _format_wrapped_list_value(values: list[str | None]) -> str | None:
    value = _format_list_value(values)
    return f"<{value}>" if value else None


def _format_substance_mixture_value(values: list[str | None]) -> str | None:
    present = [value.strip() for value in values if value and value.strip()]
    if not present:
        return None
    return f"<{'/'.join(present)}>"


def _format_salt_mixture_value(values: list[str | None]) -> str | None:
    normalized = [value.strip() if value and value.strip() else None for value in values]
    if not any(normalized):
        return None
    return _format_wrapped_list_value(
        [value if value else "unknown" for value in normalized]
    )


def _format_list_item(value: str) -> str:
    if not any(char in value for char in ',[]"'):
        return value
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow([value])
    return buffer.getvalue()


@functools.lru_cache(maxsize=1)
def _known_salt_keys() -> frozenset[str]:
    keys = {_alias_key(choice) for choice in SALT_CHOICES}
    keys.update(_alias_key(choice) for choice in SALT_ALIASES)
    return frozenset(keys)


def _is_known_salt_choice(salt: str) -> bool:
    resolved = _resolve_alias(salt, SALT_ALIASES)
    if not resolved:
        return False
    return _alias_key(resolved) in _known_salt_keys()


def _format_salt_for_log(salt: str | None) -> str | None:
    if not salt:
        return None
    return salt.strip()


def _format_mixture(
    substances: list[str],
    dosages: list[str],
    salts: list[str | None],
    volume_ml: str | None,
) -> tuple[str, str, str | None]:
    sub_len = 0
    for sub in substances:
        if not sub is None:
            sub_len += 1
    dos_len = 0
    for dos in dosages:
        if not dos is None:
            dos_len += 1
    sal_len = 0
    for sal in salts:
        if not sal is None:
            sal_len += 1
    if sub_len != dos_len:
        raise typer.BadParameter("Mixture substance and dosage counts must match")
    if sal_len > sub_len:
        raise typer.BadParameter("Mixture salt count cannot exceed substance count")
    if sal_len < sub_len:
        salts = [*salts, *([None] * (len(substances) - len(salts)))]

    resolved_substances = [
        _resolve_alias(substance, SUBSTANCE_ALIASES) or substance
        for substance in substances
    ]
    resolved_salts = [
        _format_salt_for_log(_resolve_alias(salt, SALT_ALIASES)) if salt else None
        for salt in salts
    ]
    resolved_dosages = [_format_dosage_expression(dosage) for dosage in dosages]
    dosage_value = _format_wrapped_list_value(resolved_dosages)
    volume_ml = _format_solution_volume(volume_ml)
    if volume_ml:
        dosage_value = f"{dosage_value}@{volume_ml}"
    return (
        _format_substance_mixture_value(resolved_substances),
        dosage_value,
        _format_salt_mixture_value(resolved_salts),
    )


def _split_iv_site(site: str | None) -> tuple[str | None, str | None]:
    if not site:
        return None, None
    for side in IV_SITE_SIDES:
        prefix = f"{side}-"
        if site.startswith(prefix):
            return site.removeprefix(prefix), side
    return site, None


def _format_iv_site(vein: str | None, side: str | None) -> str | None:
    if not vein:
        return None
    if side:
        return f"{side}-{vein}"
    return vein


def _is_unknown_site(site: str | None) -> bool:
    return _is_skip_choice(site)


def _logged_site(site: str | None) -> str | None:
    if _is_unknown_site(site):
        return ""
    return site


def _plain_markdown_link_label(value: str) -> str:
    match = re.fullmatch(r"\[(?P<label>[^\]]+)\]\([^)]+\)", value.strip())
    return match.group("label") if match else value


def _iv_site_key(vein: str) -> str:
    return _plain_markdown_link_label(vein).strip().casefold().replace("_", "-").replace(" ", "-")


def _format_webhook_site(roa: str, site: str | None) -> str | None:
    if not site or not _is_intravenous_route(roa):
        return site

    vein, side = _split_iv_site(site)
    if not vein:
        return site

    article = IV_SITE_WIKIPEDIA_ARTICLES.get(_iv_site_key(vein))
    if not article:
        return site

    label = _format_markdown_link_label(_plain_markdown_link_label(vein))
    linked_vein = f"[{label}](https://en.wikipedia.org/wiki/{article})"
    return f"{side}-{linked_vein}" if side else linked_vein


MARKDOWN_LINK_RE = re.compile(
    r"\[(?P<label>(?:\\.|[^\[\]\\])*)\]\((?:[^()\s]|\([^()]*\))*\)"
)


def _strip_markdown_links(text: str) -> str:
    def plain_label(match: re.Match) -> str:
        return re.sub(r"\\(.)", r"\1", match.group("label"))

    return MARKDOWN_LINK_RE.sub(plain_label, text)


def _send_notification(description: str) -> None:
    description = _strip_markdown_links(description)
    if not shutil.which("notify-send"):
        err_con.print("Notification failed: 'notify-send was not found in PATH'", style="bold yellow")
        return
    try:
        completed = subprocess.run(
            ["notify-send", "logpy", description],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "notify-send failed")
    except Exception as exc:
        err_con.print(f"Notification failed: '{exc}'", style="bold yellow")


def _increment_site_counts(
    counts: dict[str, list[int]],
    key: str,
    row_date: date,
    today: date,
) -> None:
    if not key:
        return
    site_counts = counts.setdefault(key, [0, 0, 0, 0])
    if row_date == today:
        site_counts[0] += 1
    if row_date >= today - timedelta(days=6):
        site_counts[1] += 1
    if row_date >= today - timedelta(days=29):
        site_counts[2] += 1
    site_counts[3] += 1


def _read_site_usage_counts(logfile: Path) -> dict[str, tuple[int, int, int, int]]:
    if not logfile.exists():
        return {}

    today = datetime.now(timezone.utc).date()
    counts: dict[str, list[int]] = {}
    with open(logfile, newline="") as infile:
        for row in csv.reader(infile):
            if len(row) <= 5 or row[0].strip().lower() == "timestamp":
                continue
            row_date = _parse_log_timestamp_date(row[0])
            if row_date is None:
                continue
            roa = _format_route_for_log(row[4])
            if not _is_intravenous_route(roa):
                continue
            site = row[5].strip()
            if not site:
                continue
            vein, side = _split_iv_site(site)
            if not vein:
                continue
            vein_key = _iv_site_key(vein)
            _increment_site_counts(counts, vein_key, row_date, today)
            if side:
                _increment_site_counts(
                    counts,
                    _format_iv_site(vein_key, side) or "",
                    row_date,
                    today,
                )

    return {key: (value[0], value[1], value[2], value[3]) for key, value in counts.items()}


def _format_site_usage_label(usage: tuple[int, int, int, int]) -> str:
    return f"{{d:{usage[0]} w:{usage[1]} m:{usage[2]} t:{usage[3]}}}"


def _site_choice_label(value: str, count_key: str, counts: dict[str, tuple[int, int, int, int]]) -> str:
    usage = counts.get(count_key, (0, 0, 0, 0))
    return f"{value} {_format_site_usage_label(usage)}"


def _prompt_for_site(roa: str, current: str | None = None) -> str | None:
    if not _is_intravenous_route(roa):
        return current
    current_vein, current_side = _split_iv_site(current)
    vein = _prompt_for_value("Vein", current_vein, optional=True)
    if not vein:
        return None
    if _is_unknown_site(vein):
        return UNKNOWN_SITE_CHOICE
    side = _prompt_for_value("Side", current_side, optional=False)
    return _format_iv_site(vein, side)


def _menu_for_site(
    executable: str,
    logfile: Path,
    roa: str,
    current: str | None = None,
    allow_back: bool = False,
) -> str | None:
    if not _is_intravenous_route(roa):
        return current
    current_vein, current_side = _split_iv_site(current)
    site_counts = _read_site_usage_counts(logfile)
    vein_candidates = [current_vein] if current_vein else []
    vein_candidates.append(UNKNOWN_SITE_CHOICE)
    vein_candidates.extend(IV_SITE_BASES)
    while True:
        vein = _menu_for_value(
            executable,
            "Vein",
            current_vein,
            candidates=vein_candidates,
            optional=True,
            allow_back=allow_back,
        )
        if not vein:
            return None
        if _is_unknown_site(vein):
            return UNKNOWN_SITE_CHOICE
        side_candidates = [current_side] if current_side else []
        side_candidates.extend(IV_SITE_SIDES)
        side_labels = {
            side: _site_choice_label(side, _format_iv_site(_iv_site_key(vein), side) or "", site_counts)
            for side in side_candidates
        }
        try:
            side = _menu_for_value(
                executable,
                "Side",
                current_side,
                candidates=side_candidates,
                labels_by_value=side_labels,
                optional=False,
                allow_back=allow_back,
            )
        except BackRequested:
            continue
        return _format_iv_site(vein, side)


def _read_logged_routes_by_recency(logfile: Path) -> list[str]:
    if not logfile.exists():
        return []

    routes: list[tuple[datetime, str]] = []
    with open(logfile, newline="") as infile:
        for row in csv.reader(infile):
            if len(row) <= 4 or row[0].strip().lower() == "timestamp":
                continue
            parsed_time = _parse_log_timestamp(row[0])
            route = _format_route_for_log(row[4])
            if not parsed_time or not route:
                continue
            routes.append((parsed_time, route))

    seen = set()
    ordered = []
    for _, route in sorted(routes, key=lambda item: item[0], reverse=True):
        key = _alias_key(route)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(route)
    return ordered


def _route_candidates_by_recency(logfile: Path, current: str | None = None) -> list[str]:
    candidates = _read_logged_routes_by_recency(logfile)
    seen = {_alias_key(candidate) for candidate in candidates}
    for route in [current, *ROA_ALIASES]:
        if not route:
            continue
        canonical = _format_route_for_log(route)
        if not canonical:
            continue
        key = _alias_key(canonical)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(canonical)
    return candidates


def _menu_for_route(executable: str, logfile: Path, current: str | None = None, allow_back: bool = False) -> str:
    roa_candidates = _route_candidates_by_recency(logfile, current)
    current = _format_route_for_log(current)
    roa = _menu_for_value(
        executable,
        "Route",
        current,
        candidates=roa_candidates,
        aliases=ROA_ALIASES,
        allow_back=allow_back,
    )
    if not roa:
        raise typer.BadParameter("Route of administration is required")
    return _format_route_for_log(roa) or roa.strip().casefold()


def _append_recent_unique(items: list[str], item: str, aliases: dict[str, list[str]] | None = None) -> None:
    item = item.strip()
    if not item:
        return
    comparable = _resolve_alias(item, aliases or {}) or item
    key = _alias_key(comparable)
    items[:] = [existing for existing in items if _alias_key(_resolve_alias(existing, aliases or {}) or existing) != key]
    #err_con.print(f"adding: {comparable}")
    items.insert(0, comparable)

def _scan_logged_choices(logfile: Path) -> tuple[list[str], dict[str, list[str]], list[str]]:
    if not logfile.exists():
        return [], {}, []

    # Recency-ordered, de-duplicated collections kept as dicts keyed by alias
    # key: a repeated value is moved to the end, and the dicts are reversed at
    # the end so the most recent entry comes first. This is linear in the
    # number of rows, unlike re-scanning lists for every row.
    def touch(recent: dict[str, str], value: str) -> None:
        value = value.strip()
        if not value:
            return
        key = _alias_key(value)
        recent.pop(key, None)
        recent[key] = value

    substances: dict[str, str] = {}
    dosages: dict[str, dict[str, str]] = {}
    salts: dict[str, str] = {}
    with open(logfile, newline="") as infile:
        for row in csv.reader(infile):
            if len(row) <= 3 or row[2].strip().lower() in ("substance", "med"):
                continue

            row_substances = [
                _resolve_alias(item, SUBSTANCE_ALIASES) or item
                for item in _split_mixture_values(row[2])
            ]
            row_dosages = _split_mixture_values(_dosage_without_volume(row[3]))
            row_salts = _split_mixture_values(row[6] if len(row) > 6 else None)

            for row_substance in row_substances:
                touch(substances, row_substance)

            for row_substance, row_dosage in zip(row_substances, row_dosages):
                touch(dosages.setdefault(row_substance.strip(), {}), row_dosage)

            for row_salt in row_salts:
                if not _is_known_salt_choice(row_salt):
                    touch(salts, row_salt)

    return (
        list(reversed(substances.values())),
        {substance: list(reversed(recent.values())) for substance, recent in dosages.items()},
        list(reversed(salts.values())),
    )

def _sync_choice_cache_from_history(logfile: Path) -> tuple[list[str], dict[str, list[str]], list[str]]:
    substances, dosages, salts = _scan_logged_choices(logfile)
    _store_log_choices(logfile, substances, dosages, salts)
    return substances, dosages, salts

def _read_logged_dosages_for_substance(logfile: Path, substance: str) -> list[str]:
    cached_choices = _load_cached_log_choices(logfile)
    if cached_choices is not None:
        target_key = _alias_key(_resolve_alias(substance, SUBSTANCE_ALIASES) or substance)
        for cached_substance, cached_dosages in _valid_prompt_option_dosages(
            cached_choices.get("dosages")
        ).items():
            cached_key = _alias_key(_resolve_alias(cached_substance, SUBSTANCE_ALIASES) or cached_substance)
            if cached_key == target_key:
                return cached_dosages
        return []

    if not logfile.exists():
        return []

    target_key = _alias_key(_resolve_alias(substance, SUBSTANCE_ALIASES) or substance)
    dosages = []
    with open(logfile, newline="") as infile:
        for row in csv.reader(infile):
            if len(row) <= 3 or row[2].strip().lower() in ("substance", "med"):
                continue
            row_substances = [
                _resolve_alias(item, SUBSTANCE_ALIASES) or item
                for item in _split_mixture_values(row[2])
            ]
            row_dosages = _split_mixture_values(_dosage_without_volume(row[3]))
            for row_substance, row_dosage in zip(row_substances, row_dosages):
                if _alias_key(row_substance) == target_key:
                    dosages.append(row_dosage)

    seen = set()
    unique = []
    for dosage in reversed(dosages):
        key = _alias_key(dosage)
        if key in seen:
            continue
        seen.add(key)
        unique.append(dosage)
    return unique


def _read_logged_substances(logfile: Path) -> list[str]:
    cached_choices = _load_cached_log_choices(logfile)
    if cached_choices is not None:
        return _valid_prompt_option_substances(cached_choices.get("substances"))

    substances, _, _ = _sync_choice_cache_from_history(logfile)
    return substances


def _read_logged_salts(logfile: Path) -> list[str]:
    cached_choices = _load_cached_log_choices(logfile)
    if cached_choices is not None:
        return _valid_prompt_option_strings(cached_choices.get("salts"))
    _, _, salts = _sync_choice_cache_from_history(logfile)
    return salts


def _read_logged_volumes(logfile: Path) -> list[str]:
    if not logfile.exists():
        return []

    volumes = []
    with open(logfile, newline="") as infile:
        for row in csv.reader(infile):
            if len(row) <= 3 or row[2].strip().lower() in ("substance", "med"):
                continue
            volume = _volume_from_dosage(row[3])
            if volume:
                _append_recent_unique(volumes, volume)
    return volumes


def _read_logged_needle_gauges(logfile: Path) -> list[str]:
    if not logfile.exists():
        return []

    gauges = []
    with open(logfile, newline="") as infile:
        for row in csv.reader(infile):
            if len(row) <= 7 or row[2].strip().lower() in ("substance", "med"):
                continue
            for match in NOTE_GAUGE_RE.finditer(row[7]):
                try:
                    gauge = _format_needle_gauge(match.group("gauge"))
                except typer.BadParameter:
                    continue
                if gauge:
                    _append_recent_unique(gauges, gauge)
    return gauges


def _read_logged_needle_lengths(logfile: Path) -> list[str]:
    if not logfile.exists():
        return []

    lengths = []
    with open(logfile, newline="") as infile:
        for row in csv.reader(infile):
            if len(row) <= 7 or row[2].strip().lower() in ("substance", "med"):
                continue
            for match in NOTE_LENGTH_RE.finditer(row[7]):
                try:
                    length = _format_needle_length(match.group("length"))
                except typer.BadParameter:
                    continue
                if length:
                    _append_recent_unique(lengths, length)
    return lengths


def _read_logged_syringe_sizes(logfile: Path) -> list[str]:
    if not logfile.exists():
        return []

    sizes = []
    with open(logfile, newline="") as infile:
        for row in csv.reader(infile):
            if len(row) <= 7 or row[2].strip().lower() in ("substance", "med"):
                continue
            for match in NOTE_SYRINGE_SIZE_RE.finditer(row[7]):
                try:
                    syringe_size = _format_solution_volume(match.group("size"))
                except typer.BadParameter:
                    continue
                if syringe_size:
                    _append_recent_unique(sizes, syringe_size)
    return sizes


def _menu_substance_candidates(logfile: Path, current: str | None = None) -> list[str]:
    candidates = []
    seen = set()
    for candidate in [UNKNOWN_SUBSTANCE_CHOICE, current, *_read_logged_substances(logfile)]:
        if not candidate:
            continue
        key = _alias_key(_resolve_alias(candidate, SUBSTANCE_ALIASES) or candidate)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _collect_substances_prompt(
    substance: str | None,
    dosage: str | None,
    salt: str | None,
) -> tuple[list[str], list[str], list[str | None]]:
    substances = []
    dosages = []
    salts = []
    seeded_substances = _split_mixture_values(substance)
    seeded_dosages = _split_mixture_values(dosage)
    seeded_salts = _split_mixture_values(salt)

    index = 0
    while True:
        index += 1
        current_substance = seeded_substances[index - 1] if index <= len(seeded_substances) else None
        current_dosage = seeded_dosages[index - 1] if index <= len(seeded_dosages) else None
        current_salt = seeded_salts[index - 1] if index <= len(seeded_salts) else None

        substance_label = f"Substance[{index}]"
        default_substance = current_substance
        if index > 1 and not default_substance:
            default_substance = DONE_SUBSTANCE_CHOICE
        component_substance = _prompt_for_value(
            substance_label,
            default_substance,
            optional=False,
        )

        if _alias_key(component_substance) == _alias_key(DONE_SUBSTANCE_CHOICE):
            break

        substances.append(_resolve_alias(component_substance, SUBSTANCE_ALIASES) or component_substance)
        dosages.append(_prompt_for_dosage("Dosage", current_dosage))
        salts.append(
            _resolve_alias(
                _prompt_for_value("Form", current_salt, optional=True),
                SALT_ALIASES,
            )
        )

    return substances, dosages, salts


def _collect_substances_menu(
    executable: str,
    logfile: Path,
    substance: str | None,
    dosage: str | None,
    salt: str | None,
    allow_back: bool = False,
) -> tuple[list[str], list[str], list[str | None]]:
    substances = []
    dosages = []
    salts = []
    seeded_substances = _split_mixture_values(substance)
    seeded_dosages = _split_mixture_values(dosage)
    seeded_salts = _split_mixture_values(salt)

    index = 0
    while True:
        index += 1
        while True:
            current_substance = seeded_substances[index - 1] if index <= len(seeded_substances) else None
            current_dosage = seeded_dosages[index - 1] if index <= len(seeded_dosages) else None
            current_salt = seeded_salts[index - 1] if index <= len(seeded_salts) else None
            candidates = _menu_substance_candidates(logfile, current_substance)
            selected_substance_keys = {
                _alias_key(_resolve_alias(selected_substance, SUBSTANCE_ALIASES) or selected_substance)
                for selected_substance in substances
            }
            candidates = [
                candidate
                for candidate in candidates
                if (
                    candidate == current_substance
                    or _alias_key(_resolve_alias(candidate, SUBSTANCE_ALIASES) or candidate)
                    not in selected_substance_keys
                )
            ]
            if index > 1:
                candidates = [DONE_SUBSTANCE_CHOICE, *candidates]

            try:
                component_substance = _menu_for_value(
                    executable,
                    f"Substance[{index}]",
                    DONE_SUBSTANCE_CHOICE if index > 1 else current_substance,
                    candidates=candidates,
                    aliases=SUBSTANCE_ALIASES,
                    optional=index > 1,
                    allow_back=allow_back,
                )
            except BackRequested:
                if index == 1:
                    raise
                if substances:
                    substances.pop()
                if dosages:
                    dosages.pop()
                if salts:
                    salts.pop()
                index = max(index - 2, 0)
                break
            if component_substance is None or _alias_key(component_substance) == _alias_key(DONE_SUBSTANCE_CHOICE):
                return substances, dosages, salts

            while True:
                try:
                    component_dosage = _menu_for_dosage(
                        executable,
                        "Dosage",
                        current_dosage,
                        candidates=_read_logged_dosages_for_substance(logfile, component_substance),
                        allow_back=allow_back,
                    )
                except BackRequested:
                    break

                salt_candidates = [current_salt] if current_salt else []
                salt_candidates.extend(SALT_CHOICES)
                salt_candidates.extend(_read_logged_salts(logfile))
                try:
                    component_salt = _menu_for_value(
                        executable,
                        "Form",
                        current_salt,
                        candidates=salt_candidates,
                        optional=True,
                        aliases=SALT_ALIASES,
                        allow_back=allow_back,
                    )
                except BackRequested:
                    continue

                substances.append(component_substance)
                dosages.append(component_dosage)
                salts.append(component_salt)
                break
            else:
                continue
            if len(substances) == index:
                break
        if len(substances) != index:
            index = max(index - 1, 0)
            continue

    return substances, dosages, salts


def _format_single_solution_component(
    substance: str,
    dosage: str,
    salt: str | None,
    volume: str | None,
) -> tuple[str, str, str | None]:
    return (
        _resolve_alias(substance, SUBSTANCE_ALIASES) or substance,
        _append_solution_volume(_format_dosage_expression(dosage), volume),
        _format_salt_for_log(_resolve_alias(salt, SALT_ALIASES)) if salt else None,
    )


def _format_collected_substances(
    substances: list[str],
    dosages: list[str],
    salts: list[str | None],
    volume: str | None,
) -> tuple[LogKind, str, str, str | None]:
    if len(substances) > 1:
        substance, dosage, salt = _format_mixture(substances, dosages, salts, volume)
        return LogKind.mixture, substance, dosage, salt

    substance, dosage, salt = _format_single_solution_component(
        substances[0],
        dosages[0],
        salts[0] if salts else None,
        volume,
    )
    kind = LogKind.solution if "@" in dosage else LogKind.single
    return kind, substance, dosage, salt


def _collect_input(
    mode: InputMode,
    logfile: Path,
    substance: str | None,
    dosage: str | None,
    roa: str | None,
    site: str | None,
    salt: str | None,
    note: str | None,
    time: str | None,
    volume: str | None,
    needle_gauge: str | None,
    needle_length: str | None,
    syringe_size: str | None,
    allow_back_to_menu: bool = False,
) -> tuple[LogKind, str, str, str, str | None, str | None, str | None, str | None]:
    if mode == InputMode.flags:
        substances = _split_mixture_values(substance)
        dosages = _split_mixture_values(dosage)
        salts = _split_mixture_values(salt)
        is_mixture = len(substances) > 1 or len(dosages) > 1 or len(salts) > 1
        roa = _format_route_for_log(roa)

        missing = [
            label
            for label, value in (
                ("substance", substance),
                ("dosage", dosage),
                ("roa", roa),
            )
            if not value
        ]
        if missing:
            raise typer.BadParameter(f"Missing required input field(s): {', '.join(missing)}")

        if is_mixture:
            substance, dosage, salt = _format_mixture(substances, dosages, salts, volume)
            if _route_uses_needle(roa):
                note = _append_note_injection_details(
                    note,
                    needle_gauge,
                    needle_length,
                    syringe_size,
                )
            return LogKind.mixture, substance, dosage, roa, site, salt, note, time

        substance = _resolve_alias(substance, SUBSTANCE_ALIASES) or substance
        salt = _format_salt_for_log(_resolve_alias(salt, SALT_ALIASES))
        dosage = _format_dosage_expression(dosage)
        if _route_supports_solution_volume(roa):
            dosage = _append_solution_volume(dosage, volume)
        if _route_uses_needle(roa):
            note = _append_note_injection_details(
                note,
                needle_gauge,
                needle_length,
                syringe_size,
            )
        kind = LogKind.solution if "@" in dosage else LogKind.single
        return kind, substance, dosage, roa, site, salt, note, time

    if mode == InputMode.prompt:
        if allow_back_to_menu:
            con.print(f"Enter {BACK_CHOICE} to return to the main menu")
        route_input = _prompt_for_value("Route", _format_route_for_log(roa))
        if allow_back_to_menu and route_input and _alias_key(route_input) == _alias_key(BACK_CHOICE):
            raise IngestionCancelled
        roa = _format_route_for_log(route_input)
        volume = _prompt_for_value("Volume", volume or UNKNOWN_VOLUME_CHOICE, optional=False)
        selected_syringe = None
        selected_needle = None
        if _route_uses_needle(roa):
            selected_syringe = _prompt_for_supply_entry("Syringe", "syringes")
            selected_needle = _prompt_for_supply_entry("Needle", "needles")
        site = _prompt_for_site(roa, site)
        substances, dosages, salts = _collect_substances_prompt(
            substance,
            dosage,
            salt,
        )
        note, time, dosage_by_volume = _prompt_for_extra_info(
            note,
            time,
            allow_dosage_by_volume=_syringe_size_from_entry(selected_syringe) is not None,
        )
        if dosage_by_volume:
            dosages = _scale_dosages_by_volume(
                dosages,
                volume,
                _syringe_size_from_entry(selected_syringe),
            )
        kind, substance, dosage, salt = _format_collected_substances(
            substances,
            dosages,
            salts,
            volume,
        )
        if _route_uses_needle(roa):
            note = _append_note_supply_entries(note, selected_syringe, selected_needle)
        return (
            kind,
            substance,
            dosage,
            roa,
            site,
            salt,
            note,
            time,
        )

    while True:
        menu = mode.value
        try:
            roa = _menu_for_route(menu, logfile, roa, allow_back=allow_back_to_menu)
        except BackRequested:
            raise IngestionCancelled from None
        while True:
            try:
                volume_ml = _menu_for_value(
                    menu,
                    "Volume",
                    volume,
                    candidates=[
                        UNKNOWN_VOLUME_CHOICE,
                        *([volume] if volume else []),
                        *_read_logged_volumes(logfile),
                    ],
                    allow_back=True,
                )
            except BackRequested:
                break
            while True:
                try:
                    if _route_uses_needle(roa):
                        selected_syringe = _menu_for_supply_entry(menu, "Syringe", "syringes", allow_back=True)
                        selected_needle = _menu_for_supply_entry(menu, "Needle", "needles", allow_back=True)
                    else:
                        selected_syringe = None
                        selected_needle = None
                except BackRequested:
                    volume = volume_ml
                    break
                while True:
                    try:
                        site = _menu_for_site(menu, logfile, roa, site, allow_back=True)
                    except BackRequested:
                        break
                    while True:
                        try:
                            substances, dosages, salts = _collect_substances_menu(
                                menu,
                                logfile,
                                substance,
                                dosage,
                                salt,
                                allow_back=True,
                            )
                        except BackRequested:
                            break
                        while True:
                            try:
                                note, time, dosage_by_volume = _menu_for_extra_info(
                                    menu,
                                    note,
                                    time,
                                    allow_dosage_by_volume=_syringe_size_from_entry(selected_syringe) is not None,
                                    allow_back=True,
                                )
                            except BackRequested:
                                break
                            formatted_dosages = (
                                _scale_dosages_by_volume(
                                    dosages,
                                    volume_ml,
                                    _syringe_size_from_entry(selected_syringe),
                                )
                                if dosage_by_volume else dosages
                            )
                            kind, substance, dosage, salt = _format_collected_substances(
                                substances,
                                formatted_dosages,
                                salts,
                                volume_ml,
                            )
                            if _route_uses_needle(roa):
                                note = _append_note_supply_entries(note, selected_syringe, selected_needle)
                            return (
                                kind,
                                substance,
                                dosage,
                                roa,
                                site,
                                salt,
                                note,
                                time,
                            )


def _require_option(label: str, value):
    if value is None:
        raise typer.BadParameter(f"Missing required option: {label}")
    return value


def _format_markdown_link_label(label: str) -> str:
    return label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _format_substance_link(title: str, label: str | None = None) -> str:
    title_slug = title.replace(" ", "_")
    link_label = _format_markdown_link_label(label or title)
    return f"[{link_label}](https://anodyne.wiki/substance/{title_slug})"


def _resolve_cached_substance_title(substance: str) -> str | None:
    lookup = _alias_lookup(SUBSTANCE_ALIASES)
    return lookup.get(_alias_key(substance))


def _lookup_single_substance_title(substance: str, accept = None) -> tuple[str, str]:
    if _is_skip_choice(substance) and accept != "strict":
        return UNKNOWN_SUBSTANCE_TITLE, UNKNOWN_SUBSTANCE_TITLE

    entry, not_found = _fetch_substance_entry_result(substance)
    if entry:
        _cache_substance_entry(entry)
        title = entry["Title"] if isinstance(entry["Title"], str) else substance
        return title, _format_substance_link(title)

    cached_title = _resolve_cached_substance_title(substance)
    if cached_title:
        return cached_title, _format_substance_link(cached_title)

    if not_found:
        err_con.print(f"Substance '{substance}' not found on AnodyneWiki", style="bold yellow")
        if accept == "confirm":
            _ =  typer.confirm("Log anyway?", abort=True)
        elif accept == "always":
            err_con.print(f"By default accepting unindexed substance")
            _ = True
        elif accept == "strict":
            err_con.print("Rejecting unindexed substance due to strict mode")
            raise typer.BadParameter(f"Substance '{substance}' is not indexed on AnodyneWiki")
        else:
            _ = False
    elif accept == "strict":
        raise typer.BadParameter(f"Could not verify substance '{substance}' on AnodyneWiki")
    return substance, substance


def _format_linked_substance_mixture(titles: list[str], linked_titles: list[str]) -> str:
    if len(titles) <= 1:
        return linked_titles[0] if linked_titles else ""

    formatted_links = []
    for title, linked_title in zip(titles, linked_titles):
        if linked_title == title:
            formatted_links.append(title)
        else:
            formatted_links.append(_format_substance_link(title))
    return f"\\<{'/'.join(formatted_links)}\\>"


def _lookup_substance_title(_kind: LogKind, substance: str, accept = None) -> tuple[str, str]:
    substances = _split_mixture_values(substance)
    if len(substances) <= 1:
        return _lookup_single_substance_title(substance, accept=accept)

    titles = []
    linked_titles = []
    for component in substances:
        title, linked_title = _lookup_single_substance_title(component, accept=accept)
        titles.append(title)
        linked_titles.append(linked_title)

    title = _format_substance_mixture_value(titles) or substance
    return title, _format_linked_substance_mixture(titles, linked_titles)


def _validate_config(param_value: Path | None):
    default_conf = _default_config_file()
    if not default_conf.exists():
        default_conf.parent.mkdir(parents=True, exist_ok=True)
        default_conf.touch()

    if not param_value:
        conf = toml_loader(default_conf)
    else:
        conf = toml_loader(param_value)

    return LogConfig.model_validate(conf).model_dump()

@app.command()
def log_ingestion(
    substance_arg: Annotated[str | None, typer.Argument(help="substance to log")] = None,
    dosage_arg: Annotated[str | None, typer.Argument(help="dosage and unit")] = None,
    roa_arg: Annotated[str | None, typer.Argument(help="route of administration")] = None,
    time_arg: Annotated[list[str] | None, typer.Argument(
        help="ingestion time, e.g. 'two hours ago' or '2025-12-04 10:00'"
    )] = None,
    substance_opt: Annotated[str | None, typer.Option(
        "--substance",
        help="substance to log",
        rich_help_panel="Input Options",
    )] = None,
    dosage_opt: Annotated[str | None, typer.Option(
        "--dosage",
        help="dosage and unit",
        rich_help_panel="Input Options",
    )] = None,
    roa_opt: Annotated[str | None, typer.Option(
        "--roa",
        help="route of administration",
        rich_help_panel="Input Options",
    )] = None,
    offset_time: Annotated[str | None, typer.Option(
        "--offset-time",
        help="relative ingestion time, e.g. '2 hours ago' or '30 m before'",
        rich_help_panel="Input Options",
    )] = None,
    set_time: Annotated[str | None, typer.Option(
        "--set-time",
        help="ingestion timestamp, e.g. '2025-12-04 10:00' or 'yesterday at 10pm'",
        rich_help_panel="Input Options",
    )] = None,
    volume_opt: Annotated[str | None, typer.Option(
        "--volume",
        help="injected/used solution volume to append to dosage for injection/rectal routes",
        rich_help_panel="Input Options",
    )] = None,
    needle_gauge_opt: Annotated[str | None, typer.Option(
        "--needle-gauge",
        help="needle gauge to append to note for injection routes",
        rich_help_panel="Input Options",
    )] = None,
    needle_length_opt: Annotated[str | None, typer.Option(
        "--needle-length",
        help="needle length to append to note for injection routes",
        rich_help_panel="Input Options",
    )] = None,
    syringe_size_opt: Annotated[str | None, typer.Option(
        "--syringe-size",
        help="maximum syringe capacity to append to note for injection routes",
        rich_help_panel="Input Options",
    )] = None,
    flags_mode: Annotated[bool, typer.Option(
        "--flags",
        help="read all ingestion fields from positional arguments and flags",
        rich_help_panel="Input Mode",
    )] = False,
    prompt_mode: Annotated[bool, typer.Option(
        "--prompt",
        help="prompt for ingestion fields on stdin",
        rich_help_panel="Input Mode",
    )] = False,
    #verbose_mode: Annotated[bool, typer.Option(
    #    "--verbose",
    #    help="enable verbose logging.",
    #    rich_help_panel="Verbose Mode",
    #)] = False,
    strict_mode: Annotated[bool, typer.Option(
        "--strict",
        help="only accept substances found in AnodyneWiki's API",
        rich_help_panel="Strict Mode",
    )] = False,
    dmenu_mode: Annotated[bool, typer.Option(
        "--dmenu", "-md",
        help="prompt for ingestion fields with dmenu selectors",
        rich_help_panel="Input Mode",
    )] = False,
    bemenu_mode: Annotated[bool, typer.Option(
        "--bemenu", "-mb",
        help="prompt for ingestion fields with bemenu selectors",
        rich_help_panel="Input Mode",
    )] = False,
    menu_tool: Annotated[str | None, typer.Option(
        "--menu-tool", "-mt",
        help=(
            "prompt for ingestion fields with a custom dmenu-style command, e.g. 'rofi -dmenu' "
            "or 'fuzzel --dmenu --prompt={prompt}'; '{prompt}' is replaced by the prompt text, "
            "otherwise '-p <prompt>' is appended"
        ),
        rich_help_panel="Input Mode",
    )] = None,
    menu_icon_color: Annotated[str, typer.Option(
        "--menu-icon-color",
        help=(
            "colour for the icons shown in fuzzel mode; the icons of fuzzel's icon theme are "
            "copied to the logpy cache with this fill colour. Pass an empty string to use the "
            "theme's icons unchanged"
        ),
        rich_help_panel="Input Mode",
    )] = MENU_ICON_COLOR,
    fuzzel_mode: Annotated[bool, typer.Option(
        "--fuzzel", "-mf",
        help=f"prompt for ingestion fields with fuzzel selectors (same as --menu-tool '{FUZZEL_MENU_TOOL}')",
        rich_help_panel="Input Mode",
    )] = False,
    sync_choices: Annotated[bool, typer.Option(
        "--sync-choices",
        help="scan the CSV log and refresh menu choices, dosages, salts, and substance abbreviations",
        rich_help_panel="Input Mode",
    )] = False,
    log_ingestion_action: Annotated[bool, typer.Option(
        "--log-ingestion",
        help="start prompt/menu mode directly in the ingestion logger, skipping the base action prompt",
        rich_help_panel="Input Mode",
    )] = False,
    stack_menu_action: Annotated[bool, typer.Option(
        "--stack-menu",
        help="start prompt/menu mode directly in the interactive supply stack editor, skipping the base action prompt",
        rich_help_panel="Input Mode",
    )] = False,
    stash_menu_action: Annotated[bool, typer.Option(
        "--stash-menu",
        help="start prompt/menu mode directly in the interactive stash editor, skipping the base action prompt",
        rich_help_panel="Input Mode",
    )] = False,
    add_syringes: Annotated[list[str] | None, typer.Option(
        "--add-syringe",
        "--add-syringes",
        help="add syringe stock to the virtual supply cache, e.g. '100x 2.5ml luer-slip nevershare white'",
        rich_help_panel="Supply Options",
    )] = None,
    add_needles: Annotated[list[str] | None, typer.Option(
        "--add-needle",
        "--add-needles",
        help="add needle stock to the virtual supply cache, e.g. '100x 30G 13mm bd microlance yellow'",
        rich_help_panel="Supply Options",
    )] = None,
    remove_syringes: Annotated[list[str] | None, typer.Option(
        "--remove-syringe",
        "--remove-syringes",
        help="remove syringe stock from the virtual supply cache, e.g. '10x 2.5ml luer-slip nevershare white'",
        rich_help_panel="Supply Options",
    )] = None,
    remove_needles: Annotated[list[str] | None, typer.Option(
        "--remove-needle",
        "--remove-needles",
        help="remove needle stock from the virtual supply cache, e.g. '10x 30G 13mm bd microlance yellow'",
        rich_help_panel="Supply Options",
    )] = None,
    show_stock: Annotated[bool, typer.Option(
        "--stack",
        help="show syringe and needle stock from the virtual supply cache",
        rich_help_panel="Supply Options",
    )] = False,
    add_stash: Annotated[list[str] | None, typer.Option(
        "--add-stash",
        help="add substance amount to the stash cache, e.g. '500mg Ketamine'",
        rich_help_panel="Stash Options",
    )] = None,
    remove_stash: Annotated[list[str] | None, typer.Option(
        "--remove-stash",
        help="remove substance amount from the stash cache, e.g. '25mg Ketamine'",
        rich_help_panel="Stash Options",
    )] = None,
    show_stash: Annotated[bool, typer.Option(
        "--stash",
        help="show substance amounts from the stash cache",
        rich_help_panel="Stash Options",
    )] = False,
    daily_usage: Annotated[str | None, typer.Option(
        "--daily-usage",
        help="sum usage for a day from the CSV log, using d.m.y format; defaults to today when passed without a value",
        rich_help_panel="Input Mode",
    )] = None,
    logfile: Annotated[Path | None, typer.Option(
        "--csv",
        help="File to write logs to",
        rich_help_panel="Configuration Options"
    )] = None,
    user: Annotated[str | None, typer.Option(
        "--user", "-u",
        help="username",
        rich_help_panel="Configuration Options"
    )] = None,
    userpage: Annotated[str | None, typer.Option(
        "--userpage", "-up",
        help="url linked to the username in the discord message",
        rich_help_panel="Configuration Options"
    )] = None,
    salt: Annotated[str | None, typer.Option("--salt", "-sa", help="salt form")] = None,
    site: Annotated[str | None, typer.Option("--site", "-si", help="site of administration")] = None,
    note: Annotated[str | None, typer.Option("--note", "-n", help="note added to discord message")] = None,
    webhook: Annotated[HttpUrl | None, typer.Option(
        "--webhook", "-w",
        parser=HttpUrl,
        help="discord webhook url",
        rich_help_panel="Configuration Options"
    )] = None,
    mouse: Annotated[bool, typer.Option(
        "--mouse/--no-mouse",
        help="enable mouse interaction in dmenu and bemenu selectors",
        rich_help_panel="Configuration Options",
        hidden=True,
    )] = True,
    notify: Annotated[bool, typer.Option(
        "--notify/--no-notify",
        help="send a desktop notification after logging an ingestion",
        rich_help_panel="Configuration Options",
    )] = False,
    config: Annotated[Path | None, typer.Option(
        "--config", "-c",
        callback=conf_callback_factory(_validate_config),
        show_default=str(Path(typer.get_app_dir("logpy")) / "config.toml"),
        envvar="LOGPY_CONFIG",
        help="configuration file",
        rich_help_panel="Configuration Options",
        is_eager=True,
    )] = None
):
    global MENU_MOUSE_ENABLED, MENU_TOOL_COMMAND, MENU_ICON_COLOR

    MENU_MOUSE_ENABLED = mouse
    MENU_ICON_COLOR = menu_icon_color.strip()
    _ensure_userpage_cache(userpage)
    if fuzzel_mode and menu_tool is not None:
        raise typer.BadParameter("Choose only one input mode")
    MENU_TOOL_COMMAND = _parse_menu_tool(FUZZEL_MENU_TOOL if fuzzel_mode else menu_tool)
    if menu_tool is not None and not MENU_TOOL_COMMAND:
        raise typer.BadParameter("--menu-tool must name the menu command")
    started_at = datetime.now(timezone.utc)
    has_ingestion_input = any(
        (
            substance_arg,
            dosage_arg,
            roa_arg,
            time_arg,
            substance_opt,
            dosage_opt,
            roa_opt,
            offset_time,
            set_time,
            volume_opt,
            needle_gauge_opt,
            needle_length_opt,
            syringe_size_opt,
            prompt_mode,
            dmenu_mode,
            bemenu_mode,
            menu_tool,
            fuzzel_mode,
            flags_mode,
            log_ingestion_action,
            stack_menu_action,
            stash_menu_action,
        )
    )

    if add_syringes:
        _print_supply_updates(
            "Added syringe supply",
            _add_supply_entries("syringes", add_syringes, _parse_syringe_supply),
        )
    if add_needles:
        _print_supply_updates(
            "Added needle supply",
            _add_supply_entries("needles", add_needles, _parse_needle_supply),
        )
    if remove_syringes:
        _print_supply_updates(
            "Removed syringe supply",
            _remove_supply_entries("syringes", remove_syringes, _parse_syringe_supply),
        )
    if remove_needles:
        _print_supply_updates(
            "Removed needle supply",
            _remove_supply_entries("needles", remove_needles, _parse_needle_supply),
        )
    if show_stock:
        _print_supply_stock()
    if add_stash:
        _print_stash_updates("Added stash", _add_stash_entries(add_stash))
    if remove_stash:
        _print_stash_updates("Removed stash", _remove_stash_entries(remove_stash))
    if show_stash:
        _print_stash()
    has_supply_operation = bool(add_syringes or add_needles or remove_syringes or remove_needles or show_stock)
    has_stash_operation = bool(add_stash or remove_stash or show_stash)
    if (has_supply_operation or has_stash_operation) and not has_ingestion_input and not sync_choices and not daily_usage:
        return

    if daily_usage:
        logfile = _require_option("--csv", logfile)
        _print_daily_usage(logfile, daily_usage)
        return

    mode = _resolve_input_mode(flags_mode, prompt_mode, dmenu_mode, bemenu_mode, False, bool(MENU_TOOL_COMMAND))
    base_prompt_action = _resolve_base_prompt_action(log_ingestion_action, stack_menu_action, stash_menu_action)
    if base_prompt_action and mode == InputMode.flags:
        raise typer.BadParameter("--log-ingestion, --stack-menu, and --stash-menu require a prompt/menu mode")
    def handle_menu_action(action: str) -> bool:
        """Run a non-ingestion action. Returns True when an ingestion should be logged."""
        if action == ACTION_EDIT_SUPPLY_STACK:
            _edit_supply_stack(mode)
            return False
        if action == ACTION_EDIT_STASH:
            _edit_stash(mode)
            return False
        if action == ACTION_STATISTICS:
            _show_statistics(mode, _require_option("--csv", logfile))
            return False
        if action == ACTION_SETTINGS:
            _edit_settings(
                mode,
                _config_file_from_option(config),
                {
                    "user": user,
                    "userpage": userpage,
                    "logfile": logfile,
                    "webhook": webhook,
                    "mouse": mouse,
                    "notify": notify,
                },
            )
            return False
        if action == ACTION_USERPAGE:
            _userpage_menu(mode, userpage)
            return False
        if action == ACTION_HISTORY:
            _show_history(mode, _require_option("--csv", logfile), webhook, userpage)
            return False
        if action != ACTION_LOG_INGESTION:
            raise typer.BadParameter(f"Unknown action: {action}")
        return True

    return_to_menu = False
    if mode != InputMode.flags and not has_supply_operation and not has_stash_operation and not sync_choices and not daily_usage:
        action = base_prompt_action or _select_prompt_action(mode)
        return_to_menu = base_prompt_action is None
        while not handle_menu_action(action):
            action = _select_prompt_action(mode)
            return_to_menu = True
            started_at = datetime.now(timezone.utc)

    _merge_cached_substance_aliases()
    if sync_choices:
        logfile = _require_option("--csv", logfile)
        synced_substances, synced_dosages, synced_salts = _sync_choices_from_history(logfile)
        con.print(
            "Synced choices: "
            f"{len(synced_substances)} substances, "
            f"{sum(len(items) for items in synced_dosages.values())} dosages, "
            f"{len(synced_salts)} non-indexed salt forms."
        )
        if not has_ingestion_input:
            return

    logfile = _require_option("--csv", logfile)
    user = _require_option("--user", user)
    while True:
        try:
            kind, substance, dosage, roa, site, salt, note, time = _collect_input(
                mode,
                logfile,
                _coalesce(substance_arg, substance_opt),
                _coalesce(dosage_arg, dosage_opt),
                _coalesce(roa_arg, roa_opt),
                site,
                salt,
                note,
                _coalesce(" ".join(time_arg) if time_arg else None, set_time),
                volume_opt,
                needle_gauge_opt,
                needle_length_opt,
                syringe_size_opt,
                allow_back_to_menu=return_to_menu,
            )
        except IngestionCancelled:
            action = _select_prompt_action(mode)
            while not handle_menu_action(action):
                action = _select_prompt_action(mode)
            started_at = datetime.now(timezone.utc)
            continue
        ingestion_time = (
            _menu_default_ingestion_time(mode, time, offset_time, started_at)
            or _parse_ingestion_time(time, offset_time)
        )
        time_now = ingestion_time.isoformat(timespec='milliseconds')
        is_backdated = datetime.now(timezone.utc) - ingestion_time > timedelta(minutes=10)
        title = substance_md = substance

        if not logfile.exists():
            logfile.parent.mkdir(parents=True, exist_ok=True)
            logfile.touch()
            con.print(f"File '{logfile}' has been created.")

        if mode == InputMode.prompt:
            accept_mode = "confirm"
        elif strict_mode == False:
            accept_mode = "always"
        else:
            accept_mode = "strict"
        title, substance_md = _lookup_substance_title(kind, substance, accept=accept_mode )
        logged_site = _logged_site(site)

        with open(logfile, "a", newline="") as of:
            log = csv.writer(of)
            log.writerow([time_now, user, title, dosage, roa, logged_site, salt, note])
        _add_logged_choice_options(logfile, title, dosage, salt)
        _print_supply_updates("Used supply", _decrement_logged_supplies(note))
        _print_stash_updates("Used stash", _decrement_logged_stash(title, dosage))

        logline = _format_logline(
            user, userpage, kind, dosage, substance_md, salt, roa, logged_site, note, time_now, is_backdated
        )

        if notify:
            _send_notification(logline)

        if webhook:
            try:
                message_id = _post_webhook_message(webhook.encoded_string(), logline)
                if message_id:
                    _store_webhook_message(logfile, time_now, message_id, webhook.encoded_string(), is_backdated)
            except Exception as e:
                err_con.print(f"Webhook failed: '{e}'", style="bold yellow")

        if not return_to_menu:
            return
        action = _select_prompt_action(mode)
        while not handle_menu_action(action):
            action = _select_prompt_action(mode)
        started_at = datetime.now(timezone.utc)
