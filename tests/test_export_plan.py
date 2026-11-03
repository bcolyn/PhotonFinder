"""The Export plan's rules and results, without Qt or a database."""
import logging
from datetime import date

from photonfinder.calibration import CalibrationCandidate, SessionKey
from photonfinder.export_plan import ExportEntry, ExportPlan, coerce_value
from photonfinder.models import File, LibraryRoot

D1 = date(2023, 1, 15)
D2 = date(2023, 1, 16)


def _file(rowid, name="f.fits"):
    f = File(root=LibraryRoot(name="r", path="/r"), path="p", name=name, size=0, mtime_millis=0)
    f.rowid = rowid
    return f


def _candidate(files, master=None, session_date=None):
    return CalibrationCandidate(session_date=session_date, count=len(files), files=files, master=master)


def _key(d, filter=None):
    return SessionKey(session_date=d, filter=filter, exposure=None)


class DarkFlats:
    """Stands in for the library: the dark-flats that go with each flat."""

    def __init__(self, by_flat=None):
        self.by_flat = by_flat or {}
        self.asked = []

    def __call__(self, flat):
        self.asked.append(flat)
        return self.by_flat.get(id(flat), [])


def _plan(sessions: dict, dark_flats=None, other_files=None) -> ExportPlan:
    lights = [f for files in sessions.values() for f in files]
    return ExportPlan(lights, sessions, dark_flats or DarkFlats(), other_files=other_files)


def _rows(plan, selections: dict[int, dict]):
    """Give each row its own candidates and select them, as the dialog does once loaded."""
    for row, by_type in selections.items():
        plan.set_candidates(row, {t: [c] for t, c in by_type.items() if c is not None and t != "DARKFLAT"})
        for t, c in by_type.items():
            plan.select(row, t, c)


def _ids(entries):
    return [(e.file.rowid, e.session_date) for e in entries]


class TestSelections:
    def test_the_first_candidate_is_selected_by_default(self):
        plan = _plan({_key(D1): [_file(1)]})
        recent, older = _candidate([_file(10)]), _candidate([_file(11)])

        plan.set_candidates(0, {"DARK": [recent, older]})

        assert plan.selections[0]["DARK"] is recent

    def test_no_candidates_selects_nothing(self):
        plan = _plan({_key(D1): [_file(1)]})

        plan.set_candidates(0, {"DARK": []})

        assert plan.selections[0]["DARK"] is None

    def test_a_preselection_picks_the_candidate_of_its_night(self):
        plan = _plan({_key(D1): [_file(1)]})
        recent = _candidate([_file(10)], session_date=D2)
        older = _candidate([_file(11)], session_date=D1)

        plan.set_candidates(0, {"DARK": [recent, older]}, {"DARK": _candidate([_file(11)], session_date=D1)})

        assert plan.selections[0]["DARK"] is older


class TestDarkFlats:
    def test_the_selected_flat_brings_its_dark_flats(self):
        flat = _candidate([_file(20)])
        dark_flat = _candidate([_file(30)])
        plan = _plan({_key(D1): [_file(1)]}, DarkFlats({id(flat): [dark_flat]}))

        plan.set_candidates(0, {"FLAT": [flat]})

        assert plan.candidates[0]["DARKFLAT"] == [dark_flat]
        assert plan.selections[0]["DARKFLAT"] is dark_flat

    def test_changing_the_flat_matches_its_dark_flats_again(self):
        flat_a, flat_b = _candidate([_file(20)]), _candidate([_file(21)])
        df_a, df_b = _candidate([_file(30)]), _candidate([_file(31)])
        plan = _plan({_key(D1): [_file(1)]}, DarkFlats({id(flat_a): [df_a], id(flat_b): [df_b]}))
        plan.set_candidates(0, {"FLAT": [flat_a, flat_b]})

        plan.select(0, "FLAT", flat_b)

        assert plan.candidates[0]["DARKFLAT"] == [df_b]
        assert plan.selections[0]["DARKFLAT"] is df_b

    def test_no_flat_means_no_dark_flat(self):
        flat = _candidate([_file(20)])
        plan = _plan({_key(D1): [_file(1)]}, DarkFlats({id(flat): [_candidate([_file(30)])]}))
        plan.set_candidates(0, {"FLAT": [flat]})

        plan.select(0, "FLAT", None)

        assert plan.selections[0]["DARKFLAT"] is None

    def test_a_master_flat_with_use_master_clears_the_dark_flat(self):
        flat = _candidate([_file(20)], master=_file(29))
        plan = _plan({_key(D1): [_file(1)]}, DarkFlats({id(flat): [_candidate([_file(30)])]}))
        plan.set_candidates(0, {"FLAT": [flat]})
        assert plan.selections[0]["DARKFLAT"] is not None

        plan.set_use_master(True)

        assert plan.selections[0]["DARKFLAT"] is None
        assert [e.file.rowid for e in plan.calibration_entries()] == [29]

    def test_a_flat_without_master_keeps_its_dark_flat_with_use_master(self):
        flat = _candidate([_file(20)])
        dark_flat = _candidate([_file(30)])
        plan = _plan({_key(D1): [_file(1)]}, DarkFlats({id(flat): [dark_flat]}))
        plan.set_use_master(True)

        plan.set_candidates(0, {"FLAT": [flat]})

        assert plan.selections[0]["DARKFLAT"] is dark_flat


class TestEntries:
    def test_lights_carry_their_session_date_in_export_order(self):
        k1, k2 = _key(D1), _key(D2)
        a, b, c = _file(1), _file(2), _file(3)
        plan = ExportPlan([c, a, b], {k1: [a, b], k2: [c]}, DarkFlats())

        assert _ids(plan.entries()) == [(3, D2), (1, D1), (2, D1)]

    def test_a_session_without_date_gives_none(self):
        plan = _plan({_key(None): [_file(1)]})

        assert _ids(plan.entries()) == [(1, None)]

    def test_without_lights_the_other_files_are_exported_as_they_are(self):
        others = [_file(7), _file(8)]
        plan = ExportPlan([], {}, DarkFlats(), other_files=others)

        assert _ids(plan.entries()) == [(7, None), (8, None)]

    def test_calibration_follows_the_lights(self):
        dark = _candidate([_file(10)])
        plan = _plan({_key(D1): [_file(1)]})
        _rows(plan, {0: {"DARK": dark}})

        assert _ids(plan.entries()) == [(1, D1), (10, D1)]

    def test_use_master_exports_the_master(self):
        plan = _plan({_key(D1): [_file(1)]})
        _rows(plan, {0: {"DARK": _candidate([_file(100)], master=_file(999))}})
        plan.set_use_master(True)

        assert [e.file.rowid for e in plan.calibration_entries()] == [999]

    def test_use_master_falls_back_to_the_subs(self):
        plan = _plan({_key(D1): [_file(1)]})
        _rows(plan, {0: {"DARK": _candidate([_file(100)])}})
        plan.set_use_master(True)

        assert [e.file.rowid for e in plan.calibration_entries()] == [100]

    def test_one_file_in_two_types_of_one_session_appears_once(self):
        shared = _file(1)
        plan = _plan({_key(D1): [_file(50)]})
        _rows(plan, {0: {"DARK": _candidate([shared]), "BIAS": _candidate([shared, _file(2)])}})

        assert [e.file.rowid for e in plan.calibration_entries()] == [1, 2]

    def test_a_dark_used_by_two_sessions_goes_to_both(self):
        """The regression this guards: the second session's copy of a shared dark was dropped."""
        dark = _file(50)
        plan = _plan({_key(D1): [_file(1)], _key(D2): [_file(2)]})
        _rows(plan, {0: {"DARK": _candidate([dark])}, 1: {"DARK": _candidate([dark])}})

        assert _ids(plan.calibration_entries()) == [(50, D1), (50, D2)]

    def test_different_darks_per_session(self):
        plan = _plan({_key(D1): [_file(1)], _key(D2): [_file(2)]})
        _rows(plan, {0: {"DARK": _candidate([_file(10)])}, 1: {"DARK": _candidate([_file(11)])}})

        assert _ids(plan.calibration_entries()) == [(10, D1), (11, D2)]


class TestSharedSession:
    def _two_sessions_one_flat(self):
        flat = [_file(20), _file(21)]
        plan = _plan({_key(D1): [_file(1)], _key(D2): [_file(2)]})
        _rows(plan, {0: {"FLAT": _candidate(flat)}, 1: {"FLAT": _candidate(flat)}})
        return plan

    def test_a_flat_picked_by_two_sessions_is_exported_once_under_the_shared_path(self):
        plan = self._two_sessions_one_flat()
        plan.set_shared(True)

        assert plan.shared_file_ids() == {20, 21}
        assert _ids(plan.entries()) == [(1, D1), (2, D2), (20, D1), (21, D1)]

    def test_without_shared_session_each_session_gets_a_copy(self):
        plan = self._two_sessions_one_flat()

        assert plan.shared_file_ids() == set()
        assert _ids(plan.entries())[2:] == [(20, D1), (21, D1), (20, D2), (21, D2)]

    def test_shared_with_use_master_shares_the_master(self):
        master = _file(99)
        plan = _plan({_key(D1): [_file(1)], _key(D2): [_file(2)]})
        subs = [_file(20)]
        _rows(plan, {0: {"DARK": _candidate(subs, master=master)}, 1: {"DARK": _candidate(subs, master=master)}})
        plan.set_shared(True)
        plan.set_use_master(True)

        assert plan.shared_file_ids() == {99}
        assert _ids(plan.calibration_entries()) == [(99, D1), (99, D2)]
        assert _ids(plan.entries())[2:] == [(99, D1)]


class TestCustomHeaders:
    def test_no_headers(self):
        plan = _plan({_key(D1): [_file(1)]})

        assert plan.file_headers() == {}

    def test_applied_to_the_session_lights_with_coerced_values(self):
        plan = _plan({_key(D1): [_file(1)]})
        plan.set_headers(0, "INT=42\nFLOAT=3.14\nSTR=hello")

        assert plan.file_headers()[1] == {"INT": 42, "FLOAT": 3.14, "STR": "hello"}

    def test_applied_to_the_selected_calibration_subs_not_the_master(self):
        plan = _plan({_key(D1): [_file(1)]})
        _rows(plan, {0: {"DARK": _candidate([_file(100)], master=_file(101))}})
        plan.set_headers(0, "K=v")

        headers = plan.file_headers()
        assert headers[100] == {"K": "v"}
        assert 101 not in headers

    def test_the_first_session_wins_for_a_shared_file(self):
        shared = _file(50)
        plan = _plan({_key(D1): [_file(1)], _key(D2): [_file(2)]})
        _rows(plan, {0: {"DARK": _candidate([shared])}, 1: {"DARK": _candidate([shared])}})
        plan.set_headers(0, "SESSION=1")
        plan.set_headers(1, "SESSION=2")

        assert plan.file_headers()[50] == {"SESSION": 1}

    def test_malformed_and_blank_lines_are_skipped(self, caplog):
        plan = _plan({_key(D1): [_file(1)]})
        plan.set_headers(0, "VALID=yes\nNO_EQUALS\n\n   \nALSO=ok")

        with caplog.at_level(logging.WARNING):
            assert plan.file_headers()[1] == {"VALID": "yes", "ALSO": "ok"}
        assert any("NO_EQUALS" in m for m in caplog.messages)


def test_coerce_value():
    assert coerce_value("0") == 0
    assert coerce_value("0.1") == 0.1
    assert coerce_value("abc") == "abc"
    assert coerce_value("2024-09-01T15:30:00") == "2024-09-01T15:30:00"


def test_export_entry_is_a_file_and_its_session_date():
    f = _file(1)
    assert ExportEntry(f, D1) == (f, D1)
