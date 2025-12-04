# Usage

```shell
$ uv run logpy --help 

 Usage: logpy [OPTIONS]

╭─ Options ─────────────────────────────────────────────────────────────────────────────────────────────╮
│ *  --csv                 -c       PATH  File to write logs to [required]                              │
│ *  --user                -u       TEXT  username [required]                                           │
│ *  --substance           -s       TEXT  substance to log [required]                                   │
│ *  --dosage              -d       TEXT  dosage and unit [required]                                    │
│ *  --roa                 -r       TEXT  route of administration [required]                            │
│    --salt                -sa      TEXT  salt form                                                     │
│    --site                -si      TEXT  site of administration                                        │
│    --webhook             -w       TEXT  discord webhook url                                           │
│    --note                -n       TEXT  note added to discord message                                 │
│    --install-completion                 Install completion for the current shell.                     │
│    --show-completion                    Show completion for the current shell, to copy it or          │
│                                         customize the installation.                                   │
│    --help                               Show this message and exit.                                   │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────╯
