import datetime

from photonfinder.models import SearchCriteria, RootAndPath
from photonfinder.ui.session import Session, sessions_to_json, json_to_sessions


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
