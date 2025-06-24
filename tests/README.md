# AstroFileManager Testing

This directory contains tests for the AstroFileManager application. The tests are written using pytest and are organized by component.

## Test Structure

- `conftest.py`: Contains shared fixtures and test configuration
- `test_models.py`: Tests for database models
- `test_filesystem.py`: Tests for filesystem operations

## Running Tests

### Prerequisites

Make sure you have the required testing dependencies installed:

```bash
pip install -r requirements_dev.txt
```

### Running All Tests

To run all tests:

```bash
pytest tests/
```

### Running Specific Test Files

To run tests from a specific file:

```bash
pytest tests/test_models.py
```

### Running Specific Test Functions

To run a specific test function:

```bash
pytest tests/test_models.py::TestLibraryRoot::test_is_valid_path
```

### Test Coverage

To run tests with coverage reporting:

```bash
pip install pytest-cov
pytest --cov=photonfinder tests/
```

## Writing New Tests

### Model Tests

When writing tests for models:
- Use the `app_context` fixture to get a test database
- Create temporary data for testing
- Test model methods and database operations

### Filesystem Tests

When writing tests for filesystem operations:
- Use mocks to avoid actual filesystem operations
- Use temporary directories for tests that need real files
- Test file filtering and import logic

### UI Tests

When writing tests for UI components:
- Use `pytest-qt` for testing Qt applications
- Use mocks for dependencies
- For interactive tests, use the `qtbot` fixture to simulate user actions

Example:
```python
def test_button_click(qtbot):
    widget = MyWidget()
    qtbot.addWidget(widget)
    qtbot.mouseClick(widget.button, Qt.LeftButton)
    assert widget.label.text() == "Expected Result"
```
