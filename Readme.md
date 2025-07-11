# PhotonFinder

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-blue.svg)](https://github.com/your-repo/PhotonFinder)

A powerful desktop application for managing and organizing astronomical image files. PhotonFinder (also known as AstroFileManager) helps astronomers and astrophotographers efficiently search, filter, and manage their astronomical data collections.

## 🌟 Key Features

### Core Functionality
- **Tabbed Interface** - Multitasking support with multiple search tabs for efficient workflow
- **Offline Operations** - Most functions work offline; searches can be performed without original files available (perfect for external media)
- **Advanced Search & Filtering** - Narrow down searches using multiple criteria including:
  - Cone search around given coordinates
  - Exposure time, date ranges, telescope, binning, gain, temperature
  - FITS header text search
  - Custom metadata filters

### File Format Support
- **Compressed FITS Files** - Full support for gzip, bzip2, and xz compressed FITS files for space saving
- **XISF Files** - Supports XISF files with FITS keywords (as produced by PixInsight, N.I.N.A.)

### Advanced Tools
- **Batch Plate Solving** - Automated plate solving using ASTAP and Astrometry.net
- **Calibration Frame Matching** - Find matching calibration files (darks, flats) for your light frames
- **Data Export** - Export LIGHT files with calibration data for easy import into stacking programs
- **File Management** - Find files used in processing and identify unprocessed data
- **Telescopius Integration** - Check Telescopius lists for objects that have already been imaged

### Database & Library Management
- **SQLite Database** - Efficient metadata storage and retrieval
- **Library Roots** - Define and manage multiple library directories
- **Automated Scanning** - Keep your database updated with file changes
- **Backup & Restore** - Database backup and restoration capabilities

### Reporting & Analysis
- **Comprehensive Reports** - Generate metadata reports, file lists, and data usage statistics
- **Target Lists** - Create Telescopius-compatible target lists
- **Usage Analytics** - Track your imaging data and storage usage

## 🖥️ Platform Support

- **Windows** - Full support (current)
- **Linux** - Support coming soon

## 📋 Requirements

- Python 3.8+
- PySide6 (Qt for Python)
- SQLite
- Additional dependencies listed in `requirements.txt`

## 🚀 Installation

### For Users

Binary releases are published on GitHub. Those are the recommended way to run PhotonFinder. 

If instead you want to run from the python sources:

1. Clone the repository:
```bash
git clone https://github.com/your-repo/PhotonFinder.git
cd PhotonFinder
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the application:
```bash
python photonfinder/main.py
```

### For Developers

1. Clone and install development dependencies:
```bash
git clone https://github.com/your-repo/PhotonFinder.git
cd PhotonFinder
pip install -r requirements_dev.txt
```

2. Build UI files:
```bash
python setup.py build_ui
```

3. Run tests:
```bash
pytest tests/
```

## 🏁 Quick Start

1. **Create or Open Database** - Start by creating a new database or opening an existing one
2. **Add Library Roots** - Define directories containing your astronomical files
3. **Scan Libraries** - Let PhotonFinder index your files and extract metadata
4. **Search & Filter** - Use the powerful search tools to find specific files
5. **Explore Features** - Try plate solving, calibration matching, and reporting tools

## 📚 Documentation

- [Installation Guide](docs/installation.md) - Detailed installation instructions
- [Menu Items Reference](docs/menu-items.md) - Complete guide to all features and menu options
- [User Guide](docs/index.md) - Comprehensive documentation

## 🔧 Building

To create a standalone executable:

```bash
pyinstaller photonfinder.spec
```

## 🤝 Contributing

We welcome contributions! Please feel free to submit issues, feature requests, or pull requests.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

For questions, issues, or feature requests, please:
- Open an issue on GitHub
- Check the documentation in the `docs/` folder
- Review existing issues and discussions

## 🙏 Acknowledgments

- ASTAP and Astrometry.net for plate solving capabilities
- Telescopius for target list integration
