import json
import os

import pytest

from photonfinder.ui.TelescopiusCompareDialog import parse_telescopius_json, TelescopiusTarget


class TestTelescopiusCompareDialog:

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Set up test fixtures."""
        # Load sample JSON data
        sample_json_path = os.path.join(os.path.dirname(__file__), '..', 'sample_telescopius.json')
        with open(sample_json_path, 'r') as f:
            self.sample_json_data = json.load(f)

    def test_parse_telescopius_json(self):
        """Test parsing of Telescopius JSON data into tuples."""
        # Test the parse function
        result = parse_telescopius_json(self.sample_json_data)

        assert isinstance(result, list)
        assert len(result) > 0

        # Check the first few entries match expected values from sample JSON
        expected_first_entries = [
            TelescopiusTarget("NGC 2648", 8.71105576, 14.28499985),
            TelescopiusTarget("NGC 2672", 8.82272243, 19.07444382),
            TelescopiusTarget("NGC 2685", 8.92636108, 58.73472214),
            TelescopiusTarget("NGC 2655", 8.92713928, 78.22360992),
            TelescopiusTarget("NGC 2782", 9.23472214, 40.11360931)
        ]

        # Verify first 5 entries
        for i, expected in enumerate(expected_first_entries):
            assert result[i] == expected, f"Entry {i} does not match expected value"

        for entry in result:
            assert isinstance(entry, TelescopiusTarget)

    def test_parse_telescopius_json_empty_data(self):
        # Test with empty dict
        result = parse_telescopius_json({})
        assert result == []

        # Test with missing data key
        result = parse_telescopius_json({"success": True})
        assert result == []

        # Test with missing targets key
        result = parse_telescopius_json({"data": {"id": "test"}})
        assert result == []
