# PhotonFinder

A desktop application for managing astronomical image files.

## Installation

### For Users
Install the required dependencies:

```bash
pip install -r requirements.txt
```

### For Developers
Install both production and development dependencies:

```bash
pip install -r requirements_dev.txt
```

## Development

to build UI:

```bash
python setup.py build_ui
```

to run tests:

```bash
pytest tests/
```

## Building

to build exe:

```bash
pyinstaller photonfinder.spec
```
