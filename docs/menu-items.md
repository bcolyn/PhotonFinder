# Menu Items Reference

This document provides a comprehensive guide to all menu items available in PhotonFinder.

## File Menu

The File menu contains options for managing tabs, databases, libraries, and application settings.

### Tab Management
- **New Tab** - Creates a new search tab
- **Duplicate Tab** - Duplicates the current tab with its search criteria
- **Close Tab** - Closes the current tab

### Database Operations
- **Create Database** - Creates a new SQLite database for storing file metadata
- **Open Database** - Opens an existing database file
- **Backup Database** - Creates a backup copy of the current database

### Library Management
- **Scan Libraries** - Scans all configured library roots to update the database with new or changed files
- **Manage Libraries** - Opens the library management dialog to add, edit, or remove library root directories

### Application Settings
- **Settings** - Opens the application settings dialog
- **Exit** - Closes the application

## Filter Menu

The Filter menu provides various filtering options to narrow down search results based on file metadata.

### Metadata Filters
- **Exposure** - Filter files by exposure time
- **Coordinates** - Filter files by celestial coordinates (RA/Dec)
- **Date** - Filter files by capture date range
- **Telescope** - Filter files by telescope used
- **Binning** - Filter files by camera binning settings
- **Gain** - Filter files by camera gain settings
- **Temperature** - Filter files by camera temperature

### Text Filters
- **Header Text** - Filter files by searching within FITS header text

## Tools Menu

The Tools menu contains file operations and advanced processing tools.

### File Operations
- **Open File** - Opens the selected file in the default application (disabled when no file is selected)
- **Show location** - Opens the file location in the system file explorer (disabled when no file is selected)
- **Select path** - Selects the file path for copying (disabled when no file is selected)

### Data Export
- **Export file copies** - Exports copies of selected files to a specified directory

### Plate Solving
- **Plate solve (ASTAP)** - Performs plate solving using the ASTAP software
- **Plate Solve (Astrometry.net)** - Performs plate solving using the Astrometry.net service

### Calibration Frame Matching
- **Find matching darks** - Finds dark frames that match the selected light frames (disabled when no file is selected)
- **Find matching flats** - Finds flat frames that match the selected light frames (disabled when no file is selected)

## Report Menu

The Report menu provides various reporting and analysis tools.

### File Reports
- **List Files** - Generates a simple list of files matching current search criteria
- **Metadata Report** - Creates a detailed report of file metadata
- **Data Usage Reports** - Generates reports on data usage and storage statistics

### External Integration
- **Telescopius Target List** - Generates a target list compatible with Telescopius

## Help Menu

The Help menu provides access to application information and logging.

### Information and Support
- **View Log** - Opens the application log window to view system messages and errors
- **About** - Displays information about the application, version, and credits

## Context-Sensitive Menu Items

Some menu items are context-sensitive and will be enabled or disabled based on the current state:

- **File operations** (Open File, Show location, Select path) are only enabled when a file is selected
- **Calibration frame matching** tools are only enabled when appropriate files are selected
- Other menu items may have specific requirements or states that affect their availability

## Keyboard Shortcuts

Many menu items support keyboard shortcuts (indicated by the & symbol in the menu text):
- **File**: Alt+F
- **Filter**: Alt+I  
- **Tools**: Alt+T
- **Help**: Alt+H

Individual menu items may also have specific keyboard shortcuts assigned.