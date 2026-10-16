"""Plasma concentration plots for logpy.

Every ingestion is run through a one-compartment pharmacokinetic model with
first-order absorption and the summed concentration of each substance is
rendered as filled curves over time (a port of dkinplot.rb). The module only
knows about :class:`Ingestion` values; reading logpy's CSV log and turning its
dosages into milligrams happens in ``log_py.log``.

matplotlib and numpy are imported on first use so that the rest of logpy does
not pay for them at startup.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

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

HOUR = 60 * 60
DAY = 24 * HOUR

LEGEND_TITLE_MAX = 26
LEGEND_TITLE_KEEP = 23

DEFAULT_BLOOD_VOLUME = 5.0
DEFAULT_RESOLUTION = (720, 480)
DEFAULT_MAIN_FONT = "monospace,8"
DEFAULT_LEGEND_FONT = "monospace,7"

# Pixels of a plot's width not covered by the data area; the model is sampled
# once per remaining pixel column.
PLOT_MARGIN_PIXELS = 66

LOCAL_TZ = datetime.now().astimezone().tzinfo


class PlotError(ValueError):
    """Raised for unusable plot parameters or missing plotting libraries."""


@dataclass(frozen=True)
class Ingestion:
    """One dose of a substance; ``timestamp`` must be timezone aware."""

    substance: str
    timestamp: datetime
    amount_mg: float
    route: str = ""


def plot_plasma_concentrations(
    ingestions: list[Ingestion],
    output: Path | str,
    start: datetime | None = None,
    stop: datetime | None = None,
    *,
    blood_volume: float = DEFAULT_BLOOD_VOLUME,
    resolution: tuple[int, int] = DEFAULT_RESOLUTION,
    theme: str = "dark",
    text_color: str | None = None,
    grid_color: str | None = None,
    background_color: str | None = None,
    main_font: str = DEFAULT_MAIN_FONT,
    legend_font: str = DEFAULT_LEGEND_FONT,
) -> list[str]:
    """Render the estimated plasma concentrations of ``ingestions`` to ``output``.

    ``start`` defaults to the earliest ingestion and ``stop`` to now. Only
    ingestions inside that window are plotted. Returns the names of the
    substances that were plotted, sorted by name; an empty list means the
    window held no ingestions (an empty plot is still written).
    """
    _require_matplotlib()
    if blood_volume <= 0.0:
        raise PlotError("blood volume must be a positive number of liters")
    width, height = resolution
    samples = width - PLOT_MARGIN_PIXELS
    if width <= 0 or height <= 0:
        raise PlotError("resolution dimensions must be positive")
    if samples <= 0:
        raise PlotError(f"resolution too small: width must be greater than {PLOT_MARGIN_PIXELS} pixels")

    if start is None:
        if not ingestions:
            raise PlotError("no ingestions to plot and no start time given")
        start = min(ingestion.timestamp for ingestion in ingestions)
    if stop is None:
        stop = datetime.now(tz=timezone.utc)
    start = _aware(start)
    stop = _aware(stop)
    if stop <= start:
        raise PlotError("stop time must be after start time")

    colors = _theme_colors(theme, text_color, grid_color, background_color)
    grouped: dict[str, list[Ingestion]] = {}
    for ingestion in ingestions:
        if start <= _aware(ingestion.timestamp) <= stop:
            grouped.setdefault(ingestion.substance, []).append(ingestion)
    substances = sorted(grouped)

    grid = _sample_times(start, stop, samples)
    curves = [_concentration_curve(grouped[name], grid, blood_volume) for name in substances]
    _render(
        substances,
        curves,
        grid,
        start,
        stop,
        Path(output),
        width,
        height,
        colors,
        main_font,
        legend_font,
    )
    return substances


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------
def _require_matplotlib() -> None:
    try:
        import matplotlib  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PlotError(f"plotting needs matplotlib and numpy ({exc})") from exc


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=LOCAL_TZ)


def _theme_colors(
    theme: str,
    text_color: str | None,
    grid_color: str | None,
    background_color: str | None,
) -> tuple[str, str, str | None]:
    """Text, grid and background colours; explicit colours win over the theme."""
    if theme == "light":
        theme_text, theme_grid, theme_bg = "black", "grey", None
    elif theme == "dark":
        theme_text, theme_grid, theme_bg = "white", "lightgrey", DARK_BACKGROUND
    else:
        raise PlotError(f"unknown theme {theme!r}, expected 'dark' or 'light'")
    return (
        text_color if text_color is not None else theme_text,
        grid_color if grid_color is not None else theme_grid,
        background_color if background_color is not None else theme_bg,
    )


def parse_font(spec: str, default_size: float) -> tuple[str, float]:
    """Parse a gnuplot style font spec such as "monospace,8"."""
    family, _, size = spec.partition(",")
    family = family.strip() or "monospace"
    try:
        parsed_size = float(size) if size.strip() else default_size
    except ValueError:
        raise PlotError(f"invalid font size in {spec!r}") from None
    return family, parsed_size


def parse_resolution(spec: str) -> tuple[int, int]:
    """Parse a "WIDTHxHEIGHT" resolution such as "1280x720"."""
    try:
        width, height = (int(part) for part in spec.lower().split("x", 1))
    except ValueError:
        raise PlotError(f"invalid resolution {spec!r}, expected WIDTHxHEIGHT") from None
    if width <= 0 or height <= 0:
        raise PlotError(f"invalid resolution {spec!r}, dimensions must be positive")
    return width, height


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------
def _is_intravenous(route: str) -> bool:
    return route.strip().lower() == "intravenous"


def _sample_times(start: datetime, stop: datetime, samples: int) -> "np.ndarray":
    """Epoch seconds of each sample."""
    import numpy as np

    first = start.timestamp()
    step = (stop.timestamp() - first) / samples
    return first + step * np.arange(samples, dtype=float)


def _concentration_curve(ingestions: list[Ingestion], grid_seconds: "np.ndarray", blood_volume: float) -> "np.ndarray":
    """Sum the one-compartment (Bateman) curves of all ingestions on the grid."""
    import numpy as np

    conc = np.zeros_like(grid_seconds)
    vd = float(blood_volume)
    ka = KA_PER_HOUR
    for ingestion in ingestions:
        iv = _is_intravenous(ingestion.route)
        bioavail = BIOAVAIL_IV if iv else BIOAVAIL_ORAL
        k = (K_IV_NUMERATOR / vd) if iv else K_ORAL_PER_HOUR
        hours = (grid_seconds - _aware(ingestion.timestamp).timestamp()) / HOUR
        hours = np.maximum(hours, 0.0)
        kdif = k - ka
        dose = bioavail * ingestion.amount_mg
        if abs(kdif) < 1e-12:
            # Limit of the Bateman function as k -> ka.
            conc += dose * ka * hours * np.exp(-ka * hours) / vd
        else:
            conc += (dose * ka / (vd * kdif)) * (np.exp(-ka * hours) - np.exp(-k * hours))
    return conc


# --------------------------------------------------------------------------
# Time axis
# --------------------------------------------------------------------------
def _choose_time_axis(start: datetime, stop: datetime) -> tuple[int, str]:
    """Tick step (seconds) and label format, mirroring the Ruby heuristics."""
    span = (stop - start).total_seconds()
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


def _add_months(moment: datetime, months: int) -> datetime:
    month_index = moment.month - 1 + months
    return moment.replace(year=moment.year + month_index // 12, month=month_index % 12 + 1, day=1)


def _time_ticks(start: datetime, stop: datetime, step: int) -> list[datetime]:
    """Tick instants every `step` seconds, snapped to calendar boundaries like gnuplot.

    Steps of a month or more land on the first of the month, weekly steps on a
    Sunday, daily steps on midnight and hourly steps on a multiple of the step
    within the day, all in local wall-clock time.
    """
    start = start.astimezone(LOCAL_TZ)
    stop = stop.astimezone(LOCAL_TZ)
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)

    if step >= 30 * DAY:
        months = max(1, int(step // (30 * DAY)))
        tick = midnight.replace(day=1)

        def advance(moment: datetime) -> datetime:
            return _add_months(moment, months)

    else:
        if step >= 7 * DAY:
            tick = midnight - timedelta(days=(midnight.weekday() + 1) % 7)  # back to Sunday
        elif step >= DAY:
            tick = midnight
        else:
            hours = max(1, int(step // HOUR))
            tick = midnight + timedelta(hours=(start.hour // hours) * hours)

        def advance(moment: datetime) -> datetime:
            return moment + timedelta(seconds=step)

    ticks = []
    while tick <= stop:
        if tick >= start:
            ticks.append(tick)
        tick = advance(tick)
    return ticks


def _round_up_to_tick(value: float, locator_ticks) -> float:
    if len(locator_ticks) >= 2:
        step = locator_ticks[1] - locator_ticks[0]
        if step > 0:
            return math.ceil(value / step - 1e-9) * step
    return value


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _render(
    substances: list[str],
    curves: list,
    grid_seconds,
    start: datetime,
    stop: datetime,
    output: Path,
    width: int,
    height: int,
    colors: tuple[str, str, str | None],
    main_font: str,
    legend_font: str,
) -> None:
    import numpy as np
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_rgba
    from matplotlib.ticker import FixedLocator, FormatStrFormatter

    dpi = 100.0
    text_color, grid_color, background = colors
    main_family, main_size = parse_font(main_font, 8)
    legend_family, legend_size = parse_font(legend_font, 7)

    plt.rcParams["font.family"] = [main_family, "monospace"]
    plt.rcParams["font.size"] = main_size

    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.patch.set_facecolor(background if background else "white")
    ax = fig.add_subplot(111)
    ax.set_facecolor("none")

    # Margins in character cells (left, right, bottom, top). The gnuplot script
    # used 10, 1, 3.75, 0.5; these are tighter on the left and bottom and leave
    # room on the right and top so the last tick label and the curves' peaks
    # are not cut off.
    char_w = main_size * dpi / 72.0 * 0.6
    char_h = main_size * dpi / 72.0 * 1.25
    fig.subplots_adjust(
        left=min(0.45, 8 * char_w / width),
        right=max(0.55, 1 - 3 * char_w / width),
        bottom=min(0.45, 2.75 * char_h / height),
        top=max(0.55, 1 - 1.5 * char_h / height),
    )

    palette = [color if color is not None else text_color for color in GNUPLOT_COLORS]
    x_values = [
        datetime.fromtimestamp(second, tz=timezone.utc).astimezone(LOCAL_TZ) for second in grid_seconds
    ]
    x_numbers = mdates.date2num(x_values) if x_values else []

    handles, labels = [], []
    ymax = 0.0
    for index, (substance, curve) in enumerate(zip(substances, curves)):
        color = palette[index % len(palette)]
        handle = ax.fill_between(
            x_numbers,
            0.0,
            curve,
            facecolor=to_rgba(color, 0.65),
            edgecolor=color,
            linewidth=1.0,
        )
        title = substance
        if len(title) >= LEGEND_TITLE_MAX:
            title = title[:LEGEND_TITLE_KEEP] + "..."
        handles.append(handle)
        labels.append(title)
        if len(curve):
            ymax = max(ymax, float(np.max(curve)))

    # Axes ranges
    ax.set_xlim(
        mdates.date2num(start.astimezone(LOCAL_TZ)),
        mdates.date2num(stop.astimezone(LOCAL_TZ)),
    )
    if ymax <= 0.0:
        ax.set_ylim(0.0, 1.0)
    else:
        ax.set_ylim(0.0, ymax)
        ax.set_ylim(0.0, max(_round_up_to_tick(ymax, ax.get_yticks()), ymax))

    # Time axis
    step, fmt = _choose_time_axis(start, stop)
    ticks = _time_ticks(start, stop, step)
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
    # Keep the time labels clear of the bottom border.
    ax.tick_params(axis="x", pad=main_size)
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
    suffix = output.suffix[1:].lower()
    if suffix not in fig.canvas.get_supported_filetypes():
        save_kwargs["format"] = "png"
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, **save_kwargs)
    plt.close(fig)
