from astrofilemanager.ui.SearchPanel import SearchPanel


def test__format_date_datetime_utc():
    from datetime import datetime
    from datetime import timezone
    date_obs = datetime(2025, 5, 28, 21, 15, 30, tzinfo=timezone.utc)
    local_date = date_obs.astimezone(tz=None)
    local_str = "2025-05-28 23:15:30"
    assert local_str == SearchPanel._format_date(local_date) #DST

    date_obs = datetime(2019, 1, 15, 21, 15, 30, tzinfo=timezone.utc)
    local_date = date_obs.astimezone(tz=None)
    local_str = "2019-01-15 22:15:30"
    assert local_str == SearchPanel._format_date(local_date) #no DST
