import pytest

from astrofilemanager.models import *


@pytest.mark.usefixtures("app_context")
class TestImage:

    def test_get_filters(self):
        search_criteria = SearchCriteria([
            RootAndPath(1, "subdir1")
        ])
        filters = Image.get_filters(search_criteria)
        assert filters == set()
