# logpy

CLI for logging psychoactive substance ingestions to a CSV file and optionaly to a public logger (via discord webhook).

## Installation

### nix

```sh
nix shell
```

### uv
```sh
uv pip install .
```

## Configuration

logpy looks for a config file at `~/.config/logpy/config.toml` (or `$LOGPY_CONFIG`). You can also pass everything as flags.

```toml
user = "yourname"
logfile = "log.csv"
webhook = "https://discord.com/api/webhooks/..."
mouse = false # disables mouse interaction in dmenu and bemenu selectors
notify = true # sends a desktop notification after logging an ingestion
```

When `userpage` points at an AnodyneWiki user page such as `https://anodyne.wiki/user/0xea` and `~/.config/logpy/userpage.json` does not exist yet, logpy downloads `https://anodyne.wiki/api/user/0xea` to that file on its next run. A failed download prints a warning and does not block logging.

## Usage

Log through "argument" mode:

```sh
# usage: logpy <SUBSTANCE> <DOSAGE> <ROA> [TIME...] [OPTIONS] )
logpy Methamphetamine ~50mg intravenous --note "reused-syringe reused-needle omg-my-favorite"
logpy Methamphetamine 50mg intravenous --site left-cephalic --salt Hydrochloride
logpy LSD 100μg sublingual 2 hours ago
logpy LSD 100μg sublingual -sa tartrate yesterday at 10pm
logpy MDMA 120mg oral -sa hcl 2025-12-04 10:00
```

Log through "flags" mode:

```sh
logpy --flags --substance LSD --dosage 100μg --roa sublingual
logpy --flags --substance Ketamine --dosage 25mg --roa intramuscular --volume 1.5ml --syringe-size 3ml --needle-gauge 30g --needle-length 1/2in
```

Show total usage for a day from the CSV log:

```sh
logpy --daily-usage --csv log.csv
logpy --daily-usage=23.9.2026 --csv log.csv
```

Dosage prompts accept simple arithmetic expressions for numeric amounts, e.g. entering `90 / 2` and unit `mg` logs `45mg`. Expressions also work in argument mode and may be prefixed with `~` to mark an approximate dosage, e.g. `~25*2mg` logs and posts `~50mg`.

Track virtual syringe and needle supply counts:

```sh
logpy --add-syringes "100x 2.5ml luer-slip nevershare white"
logpy --add-needle "100x 30G 13mm bd microlance yellow"
logpy --remove-syringes "10x 2.5ml luer-slip nevershare white"
logpy --remove-needle "10x 30G 13mm bd microlance yellow"
logpy --stack
```

Supply counts are stored in `~/.config/logpy/supplies.json`. Prompt and menu modes select the syringe and needle from this stack for injection routes, with a reuse option for either item when no new stock should be consumed. Reused items still select or enter a syringe or needle type for the log, but they do not need to exist in the stack and do not decrement stack counts. A "Skip" option is listed first for each item, which logs the injection without recording that syringe or needle at all and leaves the stack untouched. When an injection is logged with selected stack items or matching `--syringe-size`, `--needle-gauge`, and `--needle-length` values, the matching syringe and needle counters are decremented unless the note includes `reused-syringe` or `reused-needle`.

Syringes with `non-disposable` in their supply description are logged normally but do not decrement after use, e.g. `logpy --add-syringe "1x 1ml non-disposable glass luer-lock"`.

Track remaining substance amounts in a stash:

```sh
logpy --add-stash "500mg Ketamine"
logpy --remove-stash "25mg Ketamine"
logpy --stash
```

Stash amounts are stored in `~/.config/logpy/stash.json`. When an ingestion is logged, the matching stash entry is decremented by the logged dosage amount when the dosage can be parsed as a single numeric amount with a supported unit (`g`, `mg`, `ug`/`mcg`, or `ml`). Mixture rows decrement each matching component independently.

To log several compounds combined in one syringe volume, separate substances, dosages, and optional salts with `;` or `+`; the row is logged with substance names surrounded by `< >` and separated by `/`, and list-valued dosage/salt fields formatted as bracketed comma lists, e.g. `<Ketamine/Midazolam>`, `[25mg,1mg]@1.5ml`. In prompt and selector modes, entering more than one compound logs the row as a mixture automatically.

```sh
logpy --flags --substance "Ketamine; Midazolam" --dosage "25mg; 1mg" --roa intramuscular --volume 1.5ml
```

Log through menu modes (fast navigation and a more user-friendly and forgiving interface):

```sh
# "prompt" mode
logpy --prompt

# "dmenu" mode
logpy --dmenu

# "bemenu" mode
logpy --bemenu

# "fuzzel" mode (same as --menu-tool "fuzzel --dmenu", with icons from fuzzel's icon theme on the choices)
logpy --fuzzel --menu-icon-color "#ffffff"   # default colour; pass "" to use the theme icons unchanged
logpy --fuzzel

# custom menu mode with any dmenu-style command
logpy --menu-tool "rofi -dmenu"
logpy --menu-tool "fuzzel --dmenu --prompt={prompt}"
```

In fuzzel mode the choices carry icons from fuzzel's configured icon theme (`--icon-theme` on its command line, else `icon-theme` in `fuzzel.ini`, else hicolor). Because symbolic theme icons have a fixed dark fill, logpy copies each icon to `~/.config/logpy/icons/` with the fill replaced by `--menu-icon-color`, white by default, and hands fuzzel that copy. Pass an empty colour to use the theme icons as they are.

`--menu-tool` runs the given command for every selection, feeding the choices on stdin and reading the chosen line from stdout. `{prompt}` anywhere in the command is replaced by the prompt text; without it, `-p <prompt>` is appended, which dmenu, bemenu, rofi, and wofi understand.

The main action menu also offers a "History" entry. It lists the newest 100 logged ingestions, with a "Next 100" choice at the end for older pages. Selecting an entry offers "Edit", which changes one field of that row at a time, and "Remove", which deletes the row. Both rewrite the CSV in place and rebuild the cached menu choices from the remaining rows, so a substance, dosage, or salt that no longer appears in any row stops being suggested. This is a local read of the CSV; the AnodyneWiki lookups of `--sync-choices` are not repeated.

When a webhook is configured, the id of each Discord message posted for an ingestion is stored in `~/.config/logpy/webhook-messages.json`. Editing a log through History updates that message and removing the log deletes it. Messages posted before this file existed have no stored id and are left untouched.

The "Userpage" action is a small JSON editor for `~/.config/logpy/userpage.json`. Objects and arrays open as nested menus, selecting a scalar edits it in place, and "Add key", "Add item", and "Remove" change the current level. Typed values are stored as JSON when they parse (`3`, `true`, `null`, `"text"`, `[1, 2]`) and as plain strings otherwise. Objects that carry a `Name` key, such as the entries of `TriedSubstances`, are listed by that name. Every change is written immediately, and "Close" steps back up one level, or back to the action menu at the top.

## Flags

| Flag | Short | Description |
|------|-------|-------------|
| `--flags` | | read ingestion fields from arguments/options |
| `--prompt` | | prompt for ingestion fields on stdin |
| `--dmenu` | | prompt for ingestion fields with dmenu selectors |
| `--bemenu` | | prompt for ingestion fields with bemenu selectors |
| `--fuzzel` | `-mf` | prompt for ingestion fields with fuzzel selectors, same as `--menu-tool "fuzzel --dmenu"` |
| `--menu-tool` | `-mt` | prompt for ingestion fields with a custom dmenu-style command, e.g. `rofi -dmenu`; `{prompt}` is replaced by the prompt text, otherwise `-p <prompt>` is appended |
| `--sync-choices` | | scan the CSV log and refresh menu choices, dosages, salts, and substance abbreviations |
| `--log-ingestion` | | start prompt/menu mode directly in the ingestion logger |
| `--stack-menu` | | start prompt/menu mode directly in the interactive supply stack editor |
| `--stash-menu` | | start prompt/menu mode directly in the interactive stash editor |
| `--daily-usage` | | sum usage for a day from the CSV log; optional value uses `d.m.y` format and defaults to today |
| `--add-syringe` / `--add-syringes` | | add syringe stock to the virtual supply cache |
| `--add-needle` / `--add-needles` | | add needle stock to the virtual supply cache |
| `--remove-syringe` / `--remove-syringes` | | remove syringe stock from the virtual supply cache |
| `--remove-needle` / `--remove-needles` | | remove needle stock from the virtual supply cache |
| `--stack` | | show syringe and needle stock from the virtual supply cache |
| `--add-stash` | | add substance amount to the stash cache |
| `--remove-stash` | | remove substance amount from the stash cache |
| `--stash` | | show substance amounts from the stash cache |
| `--strict` | | only accept substances found in AnodyneWiki's API |
| `--substance` | | substance to log |
| `--dosage` | | dosage and unit |
| `--roa` | | route of administration |
| `--offset-time` | | relative ingestion time, e.g. `2 hours ago` |
| `--set-time` | | ingestion timestamp |
| `--volume` | | injected/used solution volume appended to dosage for injection/rectal routes |
| `--syringe-size` | | maximum syringe capacity appended to the note for injection routes |
| `--needle-gauge` | | needle gauge appended to the note for injection routes |
| `--needle-length` | | needle length appended to the note for injection routes |
| `--salt` | `-sa` | salt form |
| `--site` | `-si` | site of administration |
| `--note` | `-n` | note, added to the Discord message and CSV |
| `--csv` | | CSV file to write to |
| `--user` | `-u` | username |
| `--webhook` | `-w` | Discord webhook URL |
| `--notify` | | send a desktop notification after logging an ingestion |
| `--config` | `-c` | config file path |

## Ingestion CSV format

Columns: `timestamp, user, substance, dosage, roa, site, salt, note`
