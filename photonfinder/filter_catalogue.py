"""Filter catalogue: each search filter declared once.

An entry owns the SearchCriteria fields it controls and everything derived from them: whether
it is active, resetting it, its filter-chip label (shown when the filter is added and when a
saved search or session is restored), its fragment of the criteria's text summary (tab
titles), its SQL conditions (used by library_query) and the MCP descriptions of its fields.
No Qt here; the search panel opens each filter's dialog and shows its chip.
"""
import dataclasses
from typing import Callable, Optional

from photonfinder.models import File, ImageStats, SearchCriteria

_DEFAULTS = {}
for _f in dataclasses.fields(SearchCriteria):
    _DEFAULTS[_f.name] = _f.default_factory if _f.default_factory is not dataclasses.MISSING else _f.default


def default_of(field_name: str):
    """A SearchCriteria field's default value, a fresh one for list fields."""
    default = _DEFAULTS[field_name]
    return default() if callable(default) else default


@dataclasses.dataclass(frozen=True)
class FilterEntry:
    key: str
    fields: tuple                                    # the SearchCriteria fields it controls
    chip_label: Callable[[SearchCriteria], str]
    summary: Callable[[SearchCriteria], Optional[str]]
    conditions: Callable[[SearchCriteria], list]     # peewee conditions over File/Image
    mcp_descriptions: dict = dataclasses.field(default_factory=dict)  # field -> description

    def is_active(self, criteria: SearchCriteria) -> bool:
        return any(getattr(criteria, name) != default_of(name) for name in self.fields)

    def reset(self, criteria: SearchCriteria) -> None:
        for name in self.fields:
            setattr(criteria, name, default_of(name))


def _number(value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def _range(low, high) -> Optional[str]:
    """"2–3.5", "≥2", "≤3.5", or None without either bound."""
    if low is not None and high is not None:
        return _number(low) if low == high else f"{_number(low)}–{_number(high)}"
    if low is not None:
        return f"≥{_number(low)}"
    if high is not None:
        return f"≤{_number(high)}"
    return None


# --- image quality -----------------------------------------------------------------------------

# (short name, criteria prefix, ImageStats column), in the order the dialog shows them.
_QUALITY_METRICS = (
    ("Stars", "star_count", ImageStats.star_count),
    ("FWHM", "fwhm", ImageStats.fwhm_median),
    ("Bg", "background", ImageStats.background_median),
    ("Bg RMS", "background_rms", ImageStats.background_rms),
    ("Elong.", "elongation", ImageStats.elongation_median),
)


def _quality_metrics(criteria: SearchCriteria) -> list:
    """The short names of the metrics the criteria constrain."""
    return [name for name, prefix, _ in _QUALITY_METRICS
            if _range(getattr(criteria, f"{prefix}_min"), getattr(criteria, f"{prefix}_max"))]


def _quality_conditions(criteria: SearchCriteria) -> list:
    """Analysed files whose statistics fall in every range given; unanalysed files never match."""
    stats = []
    for _, prefix, column in _QUALITY_METRICS:
        low, high = getattr(criteria, f"{prefix}_min"), getattr(criteria, f"{prefix}_max")
        if low is not None:
            stats.append(column >= low)
        if high is not None:
            stats.append(column <= high)
    return [File.rowid.in_(ImageStats.select(ImageStats.file).where(*stats))] if stats else []


IMAGE_QUALITY = FilterEntry(
    key="image_quality",
    fields=tuple(f"{prefix}_{bound}" for _, prefix, _ in _QUALITY_METRICS for bound in ("min", "max")),
    chip_label=lambda c: f"Quality: {', '.join(_quality_metrics(c))}",
    summary=lambda c: f"Quality: {', '.join(_quality_metrics(c))}",
    conditions=_quality_conditions,
    mcp_descriptions={
        "star_count_min": "Detected star count, inclusive. Like all the image-quality "
                          "filters (star_count, fwhm, background, background_rms, "
                          "elongation), this only matches images that have been "
                          "analysed in PhotonFinder -- unanalysed images are excluded.",
        "fwhm_min": "Median star FWHM in pixels, inclusive. Lower is sharper.",
        "background_min": "Median sky background level, inclusive, in the image's "
                          "own pixel units (ADU for integer data, 0-1 for normalised "
                          "float data) -- so only comparable across similar files.",
        "background_rms_min": "Sky background noise (RMS), inclusive, in the same pixel "
                              "units as `background_min`.",
        "elongation_min": "Median star elongation (major/minor axis ratio), inclusive; "
                          "1.0 is perfectly round, higher suggests trailing.",
    },
)

# Every catalogue entry, in the order their chips and summary fragments appear.
ENTRIES = (IMAGE_QUALITY,)


def mcp_description(field_name: str) -> Optional[str]:
    """The MCP description of a criteria field, from the entry that owns it."""
    for entry in ENTRIES:
        if field_name in entry.mcp_descriptions:
            return entry.mcp_descriptions[field_name]
    return None
