from photonfinder.ui.common import coerce_value


def test_coerce_value():
    assert coerce_value("0") == 0
    assert coerce_value("0.1") == 0.1
    assert coerce_value("abc") == "abc"
    assert coerce_value("2024-09-01T15:30:00") == "2024-09-01T15:30:00"

