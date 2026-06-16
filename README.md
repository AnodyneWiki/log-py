# logpy

CLI for logging psychoactive substance ingestions to a CSV file, with optional Discord webhook notifications.

## Installation

```sh
nix shell
```

## Configuration

logpy looks for a config file at `~/.config/logpy/config.toml` (or `$LOGPY_CONFIG`). You can also pass everything as flags.

```toml
user = "yourname"
logfile = "log.csv"
webhook = "https://discord.com/api/webhooks/..."
```

## Usage

```sh
logpy SUBSTANCE DOSAGE ROA [TIME...]
```

Basic log:
```sh
logpy LSD 100μg sublingual
logpy LSD 100μg sublingual -sa tartrate
logpy MDMA 120mg oral -sa hcl -n "taken with food"
```

With a backdated time:
```sh
logpy LSD 100μg sublingual 2 hours ago
logpy LSD 100μg sublingual -sa tartrate yesterday at 10pm
logpy MDMA 120mg oral -sa hcl 2025-12-04 10:00
```

If the ingestion time is more than 10 minutes in the past, it gets appended to the Discord message automatically.

## Options

| Flag | Short | Description |
|------|-------|-------------|
| `--salt` | `-sa` | salt form |
| `--site` | `-si` | site of administration |
| `--note` | `-n` | note, added to the Discord message and CSV |
| `--csv` | | CSV file to write to |
| `--user` | `-u` | username |
| `--webhook` | `-w` | Discord webhook URL |
| `--config` | `-c` | config file path |

## CSV format

Columns: `timestamp, user, substance, dosage, roa, site, salt, note`
