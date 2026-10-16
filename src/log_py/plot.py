#!/usr/bin/env python3
"""dkinplot - plot estimated plasma concentrations from a CSV dose log.

Python port of dkinplot.rb. Reads a CSV log in the form

    timestamp,user,med,amount,ROA,comment
    2025-12-03T08:00:31.987Z,User,Metaphedrone,50mg,Intravenous,comment

runs every ingestion through a one-compartment pharmacokinetic model with
first-order absorption and renders the summed concentration of each
substance as filled curves over time.

Dependencies: matplotlib (and numpy, which matplotlib depends on).
"""

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import to_rgba  # noqa: E402
from matplotlib.ticker import FixedLocator, FormatStrFormatter  # noqa: E402

DARK_BACKGROUND = "#101418"

# gnuplot's default line colour sequence (the "podo" set), which the Ruby
# version relies on. The last entry is black in gnuplot; we use the text
# colour instead so the eighth series stays visible on a dark background.
GNUPLOT_COLORS = [
    "#9400D3",
    "#009E73",
    "#56B4E9",
    "#E69F00",
    "#F0E442",
    "#0072B2",
    "#E51E10",
    None,  # placeholder: replaced by the text colour at plot time
]

# Pharmacokinetic constants, taken verbatim from dkinplot.rb.
BIOAVAIL_ORAL = 0.5
BIOAVAIL_IV = 1.0
KA_PER_HOUR = 0.009 * 60  # absorption rate constant (1/h)
K_ORAL_PER_HOUR = 0.07  # elimination rate constant for non-IV routes (1/h)
K_IV_NUMERATOR = 49.0  # IV elimination rate constant is 49 / blood volume

SECOND = 1
HOUR = 60 * 60
DAY = 24 * HOUR

LEGEND_TITLE_MAX = 26
LEGEND_TITLE_KEEP = 23

LOCAL_TZ = datetime.now().astimezone().tzinfo

AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)")


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="dkinplot.py",
        description="Plot estimated plasma concentrations from a CSV dose log.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable noisy output")
    parser.add_argument("-o", "--output", metavar="OUTPUT", required=True, help="Write to OUTPUT")
    parser.add_argument("-l", "--csv", metavar="FILE", required=True, help="Plot logs from FILE")
    parser.add_argument("-s", "--start", metavar="START", help="Begin plot at START")
    parser.add_argument("-t", "--stop", metavar="STOP", help="End plot at STOP")
    parser.add_argument(
        "-bv",
        "--blood-volume",
        metavar="LITERS",
        type=float,
        default=5.0,
        help="Blood-volume(in L) to base concentrations on (default: 5.0)",
    )
    parser.add_argument(
        "-r",
        "--resolution",
        metavar="RESOLUTION",
        default="720x480",
        help="Output plot in RESOLUTION, e.g. 1280x720 (default: 720x480)",
    )
    parser.add_argument(
        "--light",
        dest="theme",
        action="store_const",
        const="light",
        help="Show the plot in light theme",
    )
    parser.add_argument(
        "--dark",
        dest="theme",
        action="store_const",
        const="dark",
        help="Show the plot in dark theme (default)",
    )
    parser.add_argument("--text-color", metavar="COLOR", help="Show the text in COLOR")
    parser.add_argument("--grid-color", metavar="COLOR", help="Show the grid in COLOR")
    parser.add_argument("--background-color", metavar="COLOR", help="Show the background in COLOR")
    parser.add_argument(
        "-mf",
        "--main-font",
        metavar="FONT",
        default="monospace,8",
        help='Write plot with FONT, gnuplot style "name,size" (default: monospace,8)',
    )
    parser.add_argument(
        "-lf",
        "--legend-font",
        metavar="FONT",
        default="monospace,7",
        help='Write plot\'s legend with FONT, gnuplot style "name,size" (default: monospace,7)',
    )
    parser.set_defaults(theme="dark")
    args = parser.parse_args(argv)

    # Theme defaults; explicit colour flags win over the theme.
    if args.theme == "light":
        theme_text, theme_grid, theme_bg = "black", "grey", None
    else:
        theme_text, theme_grid, theme_bg = "white", "lightgrey", DARK_BACKGROUND
    if args.text_color is None:
        args.text_color = theme_text
    if args.grid_color is None:
        args.grid_color = theme_grid
    if args.background_color is None:
        args.background_color = theme_bg

    return parser, args


def parse_resolution(spec):
    try:
        width, height = (int(part) for part in spec.lower().split("x", 1))
    except ValueError:
        raise SystemExit(f"invalid resolution {spec!r}, expected WIDTHxHEIGHT")
    if width <= 0 or height <= 0:
        raise SystemExit(f"invalid resolution {spec!r}, dimensions must be positive")
    return width, height


def parse_font(spec, default_size):
    """Parse a gnuplot style font spec such as "monospace,8"."""
    family, _, size = spec.partition(",")
    family = family.strip() or "monospace"
    try:
        size = float(size) if size.strip() else default_size
    except ValueError:
        raise SystemExit(f"invalid font size in {spec!r}")
    return family, size


# --------------------------------------------------------------------------
# Time handling
# --------------------------------------------------------------------------
TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
)


def parse_time(text):
    """Parse a timestamp; naive values are taken as local time like Ruby's Time.parse."""
    text = text.strip()
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    parsed = None
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in TIME_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(f"unrecognised timestamp {text!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def epoch_seconds(moment):
    return moment.timestamp()


# --------------------------------------------------------------------------
# Data loading and model
# --------------------------------------------------------------------------
def load_logs(path):
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit(f"{path}: empty CSV")
        rows = []
        for row in reader:
            # Case-insensitive header access; accept "route" as alias of "ROA".
            lowered = {(key or "").strip().lower(): (value or "") for key, value in row.items()}
            if "roa" not in lowered and "route" in lowered:
                lowered["roa"] = lowered["route"]
            rows.append(lowered)
    for required in ("timestamp", "med", "amount"):
        if not rows or required not in rows[0]:
            raise SystemExit(f"{path}: missing required column {required!r}")
    return rows


def parse_amount(text):
    match = AMOUNT_RE.search(text)
    return float(match.group(1)) if match else None


def is_intravenous(route):
    return route.strip().lower() == "intravenous"


def build_dataset(rows, min_timestamp, max_timestamp, samples):
    """Group ingestions inside the window by substance, sorted by name."""
    span = (max_timestamp - min_timestamp) / samples
    activity_x = [
        (min_timestamp + span * index).strftime("%Y-%m-%dT%H:%M:%S") for index in range(samples)
    ]

    grouped = {}
    for row in rows:
        try:
            timestamp = parse_time(row["timestamp"])
        except ValueError as error:
            print(f"warning: skipping row ({error})", file=sys.stderr)
            continue
        if not (min_timestamp <= timestamp <= max_timestamp):
            continue
        amount = parse_amount(row["amount"])
        if amount is None:
            print(
                f"warning: skipping {row['med']!r} at {row['timestamp']}: "
                f"no numeric amount in {row['amount']!r}",
                file=sys.stderr,
            )
            continue
        entry = grouped.setdefault(
            row["med"],
            {
                "Substance": row["med"],
                "Ingestions": [],
                "ActivityX": activity_x,
                "ActivityY": [],
                "BioavailOral": 0.75,
                "BioavailIV": 1.0,
            },
        )
        entry["Ingestions"].append(
            {"Timestamp": timestamp, "Amount": amount, "Route": row.get("roa", "")}
        )
    return [grouped[name] for name in sorted(grouped)]


def sample_times(min_timestamp, max_timestamp, samples):
    """Epoch seconds of each sample, matching the Ruby ActivityX grid."""
    start = epoch_seconds(min_timestamp)
    step = (epoch_seconds(max_timestamp) - start) / samples
    return start + step * np.arange(samples, dtype=float)


def concentration_curve(ingestions, grid_seconds, blood_volume):
    """Sum the one-compartment (Bateman) curves of all ingestions on the grid."""
    conc = np.zeros_like(grid_seconds)
    vd = float(blood_volume)
    ka = KA_PER_HOUR
    for ingestion in ingestions:
        iv = is_intravenous(ingestion["Route"])
        bioavail = BIOAVAIL_IV if iv else BIOAVAIL_ORAL
        k = (K_IV_NUMERATOR / vd) if iv else K_ORAL_PER_HOUR
        hours = (grid_seconds - epoch_seconds(ingestion["Timestamp"])) / HOUR
        hours = np.maximum(hours, 0.0)
        kdif = k - ka
        dose = bioavail * ingestion["Amount"]
        if abs(kdif) < 1e-12:
            # Limit of the Bateman function as k -> ka.
            conc += dose * ka * hours * np.exp(-ka * hours) / vd
        else:
            conc += (dose * ka / (vd * kdif)) * (np.exp(-ka * hours) - np.exp(-k * hours))
    return conc


# --------------------------------------------------------------------------
# Plotting
# --------------------------------------------------------------------------
def choose_time_axis(min_timestamp, max_timestamp):
    """Tick step (seconds) and label format, mirroring the Ruby heuristics."""
    span = (max_timestamp - min_timestamp).total_seconds()
    if span > 8 * 30 * DAY:
        step = 30 * DAY
    elif span > 30 * DAY:
        step = 14 * DAY
    elif span > 3.5 * DAY:
        step = DAY
    else:
        step = 3 * HOUR
    fmt = "%d.%m" if span > 3.5 * DAY else "%H:%M"
    return step, fmt


def add_months(moment, months):
    month_index = moment.month - 1 + months
    return moment.replace(year=moment.year + month_index // 12, month=month_index % 12 + 1, day=1)


def time_ticks(min_timestamp, max_timestamp, step):
    """Tick instants every `step` seconds, snapped to calendar boundaries like gnuplot.

    Steps of a month or more land on the first of the month, weekly steps on a
    Sunday, daily steps on midnight and hourly steps on a multiple of the step
    within the day, all in local wall-clock time.
    """
    start = min_timestamp.astimezone(LOCAL_TZ)
    stop = max_timestamp.astimezone(LOCAL_TZ)
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)

    if step >= 30 * DAY:
        months = max(1, int(step // (30 * DAY)))
        tick = midnight.replace(day=1)

        def advance(moment):
            return add_months(moment, months)

    else:
        if step >= 7 * DAY:
            tick = midnight - timedelta(days=(midnight.weekday() + 1) % 7)  # back to Sunday
        elif step >= DAY:
            tick = midnight
        else:
            hours = max(1, int(step // HOUR))
            tick = midnight + timedelta(hours=(start.hour // hours) * hours)

        def advance(moment):
            return moment + timedelta(seconds=step)

    ticks = []
    while tick <= stop:
        if tick >= start:
            ticks.append(tick)
        tick = advance(tick)
    return ticks


def round_up_to_tick(value, locator_ticks):
    if len(locator_ticks) >= 2:
        step = locator_ticks[1] - locator_ticks[0]
        if step > 0:
            return math.ceil(value / step - 1e-9) * step
    return value


def render(dat, curves, options, min_timestamp, max_timestamp, width, height):
    dpi = 100.0
    text_color = options.text_color
    grid_color = options.grid_color
    background = options.background_color
    main_family, main_size = parse_font(options.main_font, 8)
    legend_family, legend_size = parse_font(options.legend_font, 7)

    plt.rcParams["font.family"] = [main_family, "monospace"]
    plt.rcParams["font.size"] = main_size

    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.patch.set_facecolor(background if background else "white")
    ax = fig.add_subplot(111)
    ax.set_facecolor("none")

    # gnuplot margins are expressed in character cells: 10, 1, 3.75, 0.5.
    char_w = main_size * dpi / 72.0 * 0.6
    char_h = main_size * dpi / 72.0 * 1.25
    fig.subplots_adjust(
        left=min(0.45, 10 * char_w / width),
        right=max(0.55, 1 - 1 * char_w / width),
        bottom=min(0.45, 3.75 * char_h / height),
        top=max(0.55, 1 - 0.5 * char_h / height),
    )

    palette = [color if color is not None else text_color for color in GNUPLOT_COLORS]
    x_values = [
        datetime.fromtimestamp(second, tz=timezone.utc).astimezone(LOCAL_TZ)
        for second in sample_times(min_timestamp, max_timestamp, len(dat[0]["ActivityX"]))
    ] if dat else []
    x_numbers = mdates.date2num(x_values) if x_values else []

    handles, labels = [], []
    ymax = 0.0
    for index, (entry, curve) in enumerate(zip(dat, curves)):
        color = palette[index % len(palette)]
        handle = ax.fill_between(
            x_numbers,
            0.0,
            curve,
            facecolor=to_rgba(color, 0.65),
            edgecolor=color,
            linewidth=1.0,
        )
        title = entry["Substance"]
        if len(title) >= LEGEND_TITLE_MAX:
            title = title[:LEGEND_TITLE_KEEP] + "..."
        handles.append(handle)
        labels.append(title)
        if len(curve):
            ymax = max(ymax, float(np.max(curve)))

    # Axes ranges
    ax.set_xlim(
        mdates.date2num(min_timestamp.astimezone(LOCAL_TZ)),
        mdates.date2num(max_timestamp.astimezone(LOCAL_TZ)),
    )
    if ymax <= 0.0:
        ax.set_ylim(0.0, 1.0)
    else:
        ax.set_ylim(0.0, ymax)
        ax.set_ylim(0.0, max(round_up_to_tick(ymax, ax.get_yticks()), ymax))

    # Time axis
    step, fmt = choose_time_axis(min_timestamp, max_timestamp)
    ticks = time_ticks(min_timestamp, max_timestamp, step)
    ax.xaxis.set_major_locator(FixedLocator(mdates.date2num(ticks) if ticks else []))
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt, tz=LOCAL_TZ))
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.1f"))

    # Styling to match the gnuplot script
    ax.set_ylabel("Plasma Concentration (mg/L)", color=text_color, fontsize=main_size)
    for spine in ax.spines.values():
        spine.set_linewidth(2)
        spine.set_color(text_color)
    ax.tick_params(
        axis="both",
        colors=text_color,
        direction="in",
        labelsize=main_size,
        top=False,
        right=True,
        labelright=False,
    )
    ax.grid(True, which="major", linestyle=(0, (3, 3)), linewidth=0.5, color=grid_color)
    ax.set_axisbelow(False)

    if handles:
        legend = ax.legend(
            handles,
            labels,
            loc="upper right",
            ncol=max(1, math.ceil(len(handles) / 15)),
            frameon=False,
            markerfirst=False,
            handlelength=1.0,
            handleheight=0.7,
            handletextpad=0.5,
            labelspacing=0.15,
            columnspacing=1.0,
            borderaxespad=0.3,
            prop={"family": [legend_family, "monospace"], "size": legend_size},
        )
        for text in legend.get_texts():
            text.set_color(text_color)

    save_kwargs = {"dpi": dpi, "facecolor": fig.get_facecolor()}
    suffix = options.output.rsplit(".", 1)[-1].lower() if "." in options.output else ""
    if suffix not in fig.canvas.get_supported_filetypes():
        save_kwargs["format"] = "png"
    fig.savefig(options.output, **save_kwargs)
    plt.close(fig)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main(argv=None):
    parser, options = parse_args(argv)

    rows = load_logs(options.csv)
    if not rows:
        raise SystemExit(f"{options.csv}: no log entries")

    try:
        min_timestamp = parse_time(options.start) if options.start else None
        max_timestamp = parse_time(options.stop) if options.stop else None
    except ValueError as error:
        parser.error(str(error))

    if min_timestamp is None or max_timestamp is None:
        if min_timestamp is None:
            min_timestamp = parse_time(rows[0]["timestamp"])
        if max_timestamp is None:
            max_timestamp = datetime.now(tz=LOCAL_TZ)
        print(min_timestamp.isoformat(sep=" ", timespec="seconds"))
        print(max_timestamp.isoformat(sep=" ", timespec="seconds"))

    if max_timestamp <= min_timestamp:
        raise SystemExit("stop time must be after start time")
    if options.blood_volume <= 0.0:
        raise SystemExit("blood volume must be a positive number of liters")

    width, height = parse_resolution(options.resolution)
    samples = width - 66 if width > 66 else 0
    if samples <= 0:
        raise SystemExit("resolution too small: width must be greater than 66 pixels")

    dat = build_dataset(rows, min_timestamp, max_timestamp, samples)
    if not dat:
        print("warning: no log entries inside the plotted time range", file=sys.stderr)

    grid = sample_times(min_timestamp, max_timestamp, samples)
    curves = []
    for entry in dat:
        curve = concentration_curve(entry["Ingestions"], grid, options.blood_volume)
        entry["ActivityY"] = [f"{value:.3f}" for value in curve]
        curves.append(curve)

    render(dat, curves, options, min_timestamp, max_timestamp, width, height)

    print(f"plotting: {options.csv}")

    if options.verbose:
        def default(value):
            if isinstance(value, datetime):
                return value.isoformat()
            raise TypeError(f"cannot serialise {type(value).__name__}")

        print(json.dumps(dat, indent=2, default=default, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
