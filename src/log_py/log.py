import csv
import dateparser
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer
import requests
from pydantic import BaseModel, HttpUrl, field_validator
from typer_config import toml_loader, conf_callback_factory
from rich.console import Console

con = Console()
err_con = Console(stderr=True)
app = typer.Typer(add_completion=True, no_args_is_help=True)
app_dir = Path(typer.get_app_dir("logpy"))

class LogConfig(BaseModel):
    user: str | None = None
    logfile: Path | None = None
    webhook: HttpUrl | None = None

    @field_validator("logfile", mode="after")
    def valiadte_path(cls, logfile: Path):
        logfile = logfile.expanduser()
        if not logfile.is_absolute():
            return app_dir / logfile
        return logfile

def _parse_ingestion_time(time_str: str | None) -> datetime:
    if time_str is None:
        return datetime.now(timezone.utc)
    parsed: datetime | None = dateparser.parse(
        time_str,
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TO_TIMEZONE": "UTC",
            "PREFER_DATES_FROM": "past",
        },
    )
    if parsed is None:
        raise typer.BadParameter(f"Could not parse '{time_str}' as a date/time")
    return parsed.astimezone(timezone.utc)


def _validate_config(param_value: Path | None):
    default_conf = app_dir / "config.toml"
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
    substance: Annotated[str, typer.Argument(help="substance to log")],
    dosage: Annotated[str, typer.Argument(help="dosage and unit")],
    roa: Annotated[str, typer.Argument(help="route of administration")],
    logfile: Annotated[Path, typer.Option(
        "--csv",
        help="File to write logs to",
        rich_help_panel="Configuration Options"
    )],
    user: Annotated[str, typer.Option(
        "--user", "-u",
        help="username",
        rich_help_panel="Configuration Options"
    )],
    salt: Annotated[str | None, typer.Option("--salt", "-sa", help="salt form")] = None,
    site: Annotated[str | None, typer.Option("--site", "-si", help="site of administration")] = None,
    note: Annotated[str | None, typer.Option("--note", "-n", help="note added to discord message")] = None,
    time: Annotated[list[str] | None, typer.Argument(
        help="ingestion time, e.g. 'two hours ago' or '2025-12-04 10:00'"
    )] = None,
    webhook: Annotated[HttpUrl | None, typer.Option(
        "--webhook", "-w",
        parser=HttpUrl,
        help="discord webhook url",
        rich_help_panel="Configuration Options"
    )] = None,
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
    time_now = _parse_ingestion_time(" ".join(time) if time else None).isoformat(timespec='milliseconds')
    title = substance_md = substance

    if not logfile.exists():
        logfile.parent.mkdir(parents=True, exist_ok=True)
        logfile.touch()
        con.print(f"File '{logfile}' has been created.")

    try:
        r = requests.get(f"https://anodyne.wiki/api/substance/{substance}")
        r.raise_for_status()
        if "NotFound" in r.json() and r.json()["NotFound"]:
            raise ValueError

        title: str = r.json()["Title"] if isinstance(r.json()["Title"], str) else substance
        substance_md: str = f"[{title}](https://anodyne.wiki/substance/{title.replace(" ", "_")})"
    except ValueError:
        err_con.print(f"Substance '{substance}' not found on AnodyneWiki", style="bold yellow")
    except Exception:
        err_con.print_exception()

    with open(logfile, "a", newline="") as of:
        log = csv.writer(of)
        log.writerow([time_now, user, title, dosage, roa, site, salt, note])

    if not webhook:
        return

    logline = f"{user}: {dosage} {substance_md}" + (
        f" [{salt}]" if salt else ""
    ) + f" via {roa}" + (
        f" at {site} site" if site else ""
    ) + (
        f"\n> {note}" if note else ""
    )

    try:
        p = requests.post(webhook.encoded_string(), json={ "content": logline, "flags": 4})
        p.raise_for_status()
        con.print(f"Status: {p.status_code}")
    except Exception as e:
        err_con.print(f"Webhook failed: '{e}'", style="bold yellow")
