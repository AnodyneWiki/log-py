# Usage

```
$ uv run logpy --help 

 Usage: logpy [OPTIONS] SUBSTANCE DOSAGE ROA

╭─ Arguments ───────────────────────────────────────────────────────────────────────────────────────────────────╮
│ *    substance      TEXT  substance to log [required]                                                         │
│ *    dosage         TEXT  dosage and unit [required]                                                          │
│ *    roa            TEXT  route of administration [required]                                                  │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Options ─────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --salt  -sa      TEXT  salt form                                                                              │
│ --site  -si      TEXT  site of administration                                                                 │
│ --note  -n       TEXT  note added to discord message                                                          │
│ --help                 Show this message and exit.                                                            │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Configuration Options ───────────────────────────────────────────────────────────────────────────────────────╮
│ *  --csv              PATH     File to write logs to [required]                                               │
│ *  --user     -u      TEXT     username [required]                                                            │
│    --webhook  -w      HTTPURL  discord webhook url                                                            │
│    --config   -c      PATH     configuration file                                                             │
│                                [env var: LOGPY_CONFIG]                                                        │
│                                [default: (/Users/emily/Library/Application Support/logpy/config.toml)]        │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
