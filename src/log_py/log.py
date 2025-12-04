import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer
import requests
from rich.console import Console

con = Console()
app = typer.Typer()

@app.command()
def log_ingestion(
    logfile: Annotated[Path, typer.Option("--csv", "-c", help="File to write logs to")],
    user: Annotated[str, typer.Option("--user", "-u", help="username")],
    substance: Annotated[str, typer.Option("--substance", "-s", help="substance to log")],
    dosage: Annotated[str, typer.Option("--dosage", "-d", help="dosage and unit")],
    roa: Annotated[str, typer.Option("--roa", "-r", help="route of administration")],
    salt: Annotated[str | None, typer.Option("--salt", "-sa", help="salt form")] = None,
    site: Annotated[str | None, typer.Option("--site", "-si", help="site of administration")] = None,
    webhook: Annotated[str | None, typer.Option("--webhook", "-w", help="discord webhook url")] = None,
    note: Annotated[str | None, typer.Option("--note", "-n", help="note added to discord message")] = None
):
    time_now = datetime.now(timezone.utc).isoformat(timespec='milliseconds')

    if not logfile.exists():
        logfile.touch()
        con.print(f"File {logfile} has been created.", style="blue")

    try:
        r = requests.get(f"https://anodyne.wiki/api/substance/{substance}")
        r.raise_for_status
        if "NotFound" in r.json() and r.json()["NotFound"]:
            raise

        title: str = r.json()["Title"] if isinstance(r.json()["Title"], str) else substance
        substance_md: str = f"[{title}](https://anodyne.wiki/substance/{title.replace(" ", "_")})"
    except Exception as e:
        con.print(e)
        title, substance_md = substance

    with open(logfile, "w", newline="") as of:
        log = csv.writer(of)
        log.writerow([time_now, user, title, dosage, roa, site, salt])

    if not webhook:
        return

    logline = f"{user}: {dosage} {substance_md}" + (
        f" [{salt}]" if salt else ""
    ) + f" via {roa}" + (
        f" at {site} site" if site else ""
    ) + f" {note}" if note else ""

    try:
        p = requests.post(webhook, json={ "content": logline, "flags": 4})
        con.print(f"Status: {p.status_code}")
    except Exception as e:
        con.print(f"Webhook failed: {e}", style="bold red")
