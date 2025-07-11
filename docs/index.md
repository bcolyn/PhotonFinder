# PhotonFinder Documentation

Welcome to the PhotonFinder documentation. PhotonFinder is a desktop application for managing astronomical files, allowing users to define library roots (directories) and search through files within these libraries.

## Overview

PhotonFinder (also known as AstroFileManager) is designed to help astronomers and astrophotographers organize, search, and manage their astronomical image files. The application provides powerful filtering and search capabilities to quickly locate specific files based on various metadata criteria.

## Key Features

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
- **Multiple Formats** - Handles various astronomical files produced by SGP, N.I.N.A., APT (AstroPhotography Tool) and more.

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

## Technology Stack

- **UI Framework**: PySide6 (Qt for Python)
- **Database**: SQLite with Peewee ORM
- **Packaging**: PyInstaller for creating standalone executables

## Getting Started

1. Install PhotonFinder following the [installation guide](installation.md)
2. Create or open a database to store your file metadata
3. Add library roots pointing to your astronomical file directories
4. Scan your libraries to populate the database
5. Use the search and filter features to find specific files
6. Explore the various menu options described in the [menu items guide](menu-items.md)

## Documentation Contents

- [Installation Guide](installation.md) - How to install and set up PhotonFinder
- [Menu Items Reference](menu-items.md) - Complete guide to all menu options and features

## Support

For issues, questions, or contributions, please refer to the project repository.
