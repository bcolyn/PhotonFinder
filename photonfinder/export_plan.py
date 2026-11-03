"""The Export plan: what an export of light frames with their calibration will write, without Qt.

The export dialog renders a plan and forwards the user's choices to it. The plan holds the light
sessions, the calibration candidates found for each session, which candidate is selected per
calibration type, the use-master and shared-session flags and the custom headers per session. It
applies the export rules whenever something changes, and produces the export entries, the file →
custom headers map and the set of files that go under the shared path.

Rows are the light sessions in display order (``session_keys``). Calibration types are
``CALIB_TYPES``. A dark-flat matches the selected flat, so its candidates are found again,
through ``find_dark_flats``, whenever the flat changes.
"""
import datetime
import logging
from typing import Callable, NamedTuple, Optional

from photonfinder.calibration import CalibrationCandidate, SessionKey
from photonfinder.models import File

CALIB_TYPES = ("FLAT", "DARK", "BIAS", "DARKFLAT")
_CALIBRATION_IMAGE_TYPES = {"DARK", "FLAT", "BIAS", "MASTER DARK", "MASTER FLAT", "MASTER BIAS"}

FindDarkFlats = Callable[[Optional[CalibrationCandidate]], list[CalibrationCandidate]]


class ExportEntry(NamedTuple):
    """A file paired with the session date that determines its destination folder."""
    file: File
    session_date: Optional[datetime.date]


def _is_calibration(file: File) -> bool:
    image = getattr(file, 'image', None)
    return bool(image and (image.image_type or "").upper() in _CALIBRATION_IMAGE_TYPES)


def coerce_value(value: str):
    """A header value as int, else float, else the string itself."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def parse_custom_headers(raw: str) -> dict:
    """KEY=VALUE lines into {KEY: value}, values coerced to numbers where they look like one."""
    parsed = {}
    for line in raw.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        if '=' not in line:
            logging.warning(f"Ignoring malformed custom header line (expected KEY=VALUE): {line!r}")
            continue
        k, v = line.split('=', 1)
        parsed[k.strip()] = coerce_value(v.strip())
    return parsed


class ExportPlan:
    def __init__(self, light_files: list[File], sessions: dict[SessionKey, list[File]],
                 find_dark_flats: FindDarkFlats, other_files: Optional[list[File]] = None):
        """``light_files``: the lights in export order. ``sessions``: those lights per session, in
        display order. ``other_files``: the non-light files of the export selection, exported as
        they are when there are no lights. With lights, the calibration frames among them choose
        each row's candidates: a candidate holding a selected frame (or whose master it is) is
        selected over the most recent one."""
        self.light_files = list(light_files)
        self.sessions = sessions
        self.session_keys: list[SessionKey] = list(sessions)
        self.other_files = list(other_files or [])
        self.selected_calibration = [f for f in self.other_files if _is_calibration(f)]
        self._selected_ids = {f.rowid for f in self.selected_calibration if f.rowid is not None}
        self._find_dark_flats = find_dark_flats
        self.use_master = False
        self.shared = False
        self.candidates: dict[int, dict[str, list[CalibrationCandidate]]] = {
            row: {t: [] for t in CALIB_TYPES} for row in range(len(self.session_keys))}
        self.selections: dict[int, dict[str, Optional[CalibrationCandidate]]] = {
            row: {t: None for t in CALIB_TYPES} for row in range(len(self.session_keys))}
        self.headers: dict[int, str] = {}  # row → raw KEY=VALUE text

    # --- changes -----------------------------------------------------------------------------

    def set_candidates(self, row: int, candidates: dict[str, list[CalibrationCandidate]]):
        """The candidates found for a row. Each type selects the candidate holding a selected
        calibration frame, else the first (most recent) one; then the dark-flats for the selected
        flat are found."""
        for calib_type in ("FLAT", "DARK", "BIAS"):
            cands = list(candidates.get(calib_type, []))
            self.candidates[row][calib_type] = cands
            self.selections[row][calib_type] = self._default(cands)
        self._match_dark_flats(row)

    def select(self, row: int, calib_type: str, candidate: Optional[CalibrationCandidate]):
        """The user picked ``candidate`` (None for none) for one row and type."""
        self.selections[row][calib_type] = candidate
        if calib_type == "FLAT":
            self._match_dark_flats(row)

    def set_use_master(self, use_master: bool):
        self.use_master = use_master
        for row in self.selections:
            self._apply_master_flat_rule(row)

    def set_shared(self, shared: bool):
        self.shared = shared

    def set_headers(self, row: int, text: str):
        self.headers[row] = text

    # --- rules -------------------------------------------------------------------------------

    def _holds_selected(self, candidate: CalibrationCandidate) -> bool:
        ids = {f.rowid for f in candidate.files}
        if candidate.master is not None:
            ids.add(candidate.master.rowid)
        return bool(ids & self._selected_ids)

    def _default(self, candidates: list[CalibrationCandidate]) -> Optional[CalibrationCandidate]:
        """The candidate holding a selected calibration frame, else the first (most recent)."""
        for c in candidates:
            if self._holds_selected(c):
                return c
        return candidates[0] if candidates else None

    def _match_dark_flats(self, row: int):
        """Dark-flats belong to the selected flat: find them again and select the default."""
        cands = list(self._find_dark_flats(self.selections[row]["FLAT"]))
        self.candidates[row]["DARKFLAT"] = cands
        self.selections[row]["DARKFLAT"] = self._default(cands)
        self._apply_master_flat_rule(row)

    def _apply_master_flat_rule(self, row: int):
        """A master flat is already dark-flat corrected: no dark-flat when it will be used."""
        flat = self.selections[row]["FLAT"]
        if self.use_master and flat is not None and flat.master is not None:
            self.selections[row]["DARKFLAT"] = None

    # --- results -----------------------------------------------------------------------------

    def unmatched_calibration(self) -> list[File]:
        """Selected calibration frames that no row has as a candidate (its subs or its master),
        so the export leaves them out. Nothing when there are no lights: then every selected
        file is exported as it is."""
        if not self.light_files:
            return []
        offered = set()
        for by_type in self.candidates.values():
            for cands in by_type.values():
                for c in cands:
                    offered.update(f.rowid for f in c.files)
                    if c.master is not None:
                        offered.add(c.master.rowid)
        return [f for f in self.selected_calibration if f.rowid not in offered]

    def _exported_files(self, candidate: CalibrationCandidate) -> list[File]:
        return [candidate.master] if self.use_master and candidate.master else candidate.files

    def shared_candidates(self) -> dict[str, CalibrationCandidate]:
        """{calib_type: candidate} selected by two or more sessions, when shared-session is on.
        Per type only the candidate selected by the most sessions is shared."""
        if not self.shared:
            return {}
        type_cand_map: dict[str, dict] = {}  # calib_type → {frozenset_of_file_ids: (candidate, session_count)}
        for row_sel in self.selections.values():
            for calib_type, candidate in row_sel.items():
                if candidate is None:
                    continue
                cid = frozenset(f.rowid for f in candidate.files if f.rowid is not None)
                tc = type_cand_map.setdefault(calib_type, {})
                if cid in tc:
                    existing, n = tc[cid]
                    tc[cid] = (existing, n + 1)
                else:
                    tc[cid] = (candidate, 1)
        result = {}
        for calib_type, cand_map in type_cand_map.items():
            multi = [(c, n) for c, n in cand_map.values() if n >= 2]
            if multi:
                result[calib_type] = max(multi, key=lambda x: x[1])[0]
        return result

    def shared_file_ids(self) -> set[int]:
        """The rowids of the files that go under the shared path."""
        shared_ids = set()
        for candidate in self.shared_candidates().values():
            for f in self._exported_files(candidate):
                if f.rowid is not None:
                    shared_ids.add(f.rowid)
        return shared_ids

    def calibration_entries(self) -> list[ExportEntry]:
        """One entry per (session, calibration file). The same file appears once per session that
        selected it, each with that session's date; within a session it appears once."""
        seen: set[tuple] = set()
        result: list[ExportEntry] = []
        for row, key in enumerate(self.session_keys):
            d = key.session_date
            for candidate in self.selections[row].values():
                if not candidate:
                    continue
                for f in self._exported_files(candidate):
                    pair = (f.rowid, d)
                    if pair not in seen:
                        seen.add(pair)
                        result.append(ExportEntry(file=f, session_date=d))
        return result

    def entries(self) -> list[ExportEntry]:
        """Everything the export writes: the lights with their session dates, then the
        calibration, with a file under the shared path listed only once."""
        if not self.light_files:
            return [ExportEntry(f, None) for f in self.other_files]
        session_dates = {f.rowid: key.session_date for key in self.session_keys for f in self.sessions[key]
                         if f.rowid is not None and key.session_date is not None}
        light_entries = [ExportEntry(f, session_dates.get(f.rowid)) for f in self.light_files]
        calib_entries = self.calibration_entries()
        shared_ids = self.shared_file_ids()
        if shared_ids:
            seen_shared: set[int] = set()
            deduped: list[ExportEntry] = []
            for entry in calib_entries:
                if entry.file.rowid in shared_ids:
                    if entry.file.rowid not in seen_shared:
                        seen_shared.add(entry.file.rowid)
                        deduped.append(entry)
                else:
                    deduped.append(entry)
            calib_entries = deduped
        return light_entries + calib_entries

    def file_headers(self) -> dict[int, dict]:
        """File rowid → custom FITS headers. A session's headers apply to its lights and to its
        selected calibration subs (not the master); for a file in several sessions the first wins."""
        file_headers = {}
        for row, key in enumerate(self.session_keys):
            parsed = parse_custom_headers(self.headers.get(row, ""))
            if not parsed:
                continue
            for f in self.sessions[key]:
                if f.rowid is not None:
                    file_headers[f.rowid] = parsed
            for candidate in self.selections[row].values():
                if candidate:
                    for f in candidate.files:
                        if f.rowid is not None and f.rowid not in file_headers:
                            file_headers[f.rowid] = parsed
        return file_headers
