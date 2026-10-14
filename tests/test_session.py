import datetime
import json

import pytest

from photonfinder.models import SearchCriteria, RootAndPath
from photonfinder.ui.session import Session, SessionManager, sessions_to_json, json_to_sessions


def test_roundtrip():
    sessions = [Session(
        hidden_columns="",
        title="title1",
        criteria=SearchCriteria(paths=[RootAndPath(1, "Root", "test/test2")], gain="100")
    ), Session(
        hidden_columns="col1,col2",
        title="title2",
        criteria=SearchCriteria(start_datetime=datetime.datetime.fromisoformat("20190101T000000"))
    )]
    json_str = sessions_to_json(sessions)
    assert json_str is not None
    assert len(json_str) > 0
    deser = json_to_sessions(json_str)
    assert sessions == deser


def _saved(criteria: dict, title="t") -> dict:
    return {"criteria": criteria, "title": title, "hidden_columns": "", "title_is_custom": False}


def test_deleted_reference_file_and_project_are_dropped(sample):
    _, objs = sample
    data = [_saved({"reference_file": 9999, "gain": "100"}, "stale ref"),
            _saved({"project": 9999}, "stale project"),
            _saved({"reference_file": objs["light"].rowid, "project": objs["project"].rowid}, "live")]

    sessions = json_to_sessions(json.dumps(data))

    assert [s.title for s in sessions] == ["stale ref", "stale project", "live"]
    assert sessions[0].criteria.reference_file is None
    assert sessions[0].criteria.gain == "100"
    assert sessions[1].criteria.project is None
    assert sessions[2].criteria.reference_file.rowid == objs["light"].rowid
    assert sessions[2].criteria.project.rowid == objs["project"].rowid


def test_unknown_keys_are_ignored():
    data = [{**_saved({"gain": "100", "removed_filter": 1}), "future_field": True}]

    sessions = json_to_sessions(json.dumps(data))

    assert sessions[0].criteria.gain == "100"


def test_one_broken_tab_does_not_lose_the_others():
    data = [_saved({"start_datetime": "not a date"}, "broken"), _saved({"gain": "100"}, "ok")]

    sessions = json_to_sessions(json.dumps(data))

    assert [s.title for s in sessions] == ["ok"]


def test_unreadable_session_file_is_kept_aside(tmp_path):
    file = tmp_path / "session.json"
    file.write_text("{ not json")

    assert SessionManager(str(file)).load_sessions() == []
    assert not file.exists()
    assert (tmp_path / "session.json.corrupt").read_text() == "{ not json"


def test_failed_save_keeps_previous_file(tmp_path):
    file = tmp_path / "session.json"
    file.write_text("previous")
    unserializable = Session(criteria=SearchCriteria(), title=object(), hidden_columns="")

    with pytest.raises(AttributeError):
        SessionManager(str(file)).save_sessions([unserializable])

    assert file.read_text() == "previous"
