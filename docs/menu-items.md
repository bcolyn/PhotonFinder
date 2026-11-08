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
- **Manage Libraries** - Opens the library management dialog to add, edit, or remove library root directories. Each root can carry an optional description of what it holds; it shows as a tooltip in the library tree and is exposed to MCP agents

### Application Settings
- **Settings** - Opens the application settings dialog. Under **Observatory**, the timezone is used for the local time of frames whose header has neither `DATE-LOC` nor site coordinates; it is set to the computer's timezone on first start, and changing it updates those frames in the background

### Local time and observing nights
Every frame gets a local time when it is indexed, from the first source its header has: `DATE-LOC` (the local time the capture software recorded), else `DATE-OBS` in the time zone of the observatory (`SITELAT`/`SITELONG` or `OBSGEO-B`/`OBSGEO-L`), else `DATE-OBS` in the timezone set under Settings. The grid's **Observation Date** column shows that local time (hover for UTC). The **Night** column shows the observing night: the local time minus 12 hours, as a date, so frames from before and after midnight share one night. Calibration matching, quality diagnosis and the export path tokens use the same night. After upgrading, existing frames get their local time in the background; until then their date shows in UTC.
- **Exit** - Closes the application

## Filter Menu

The Filter menu provides various filtering options to narrow down search results based on file metadata.

### Group by Night
- **Group by Night** - Shows one row per group of frames instead of one per image: a group is one target (`OBJECT`) on one observing night, with the same image type, folder, filter and exposure. Each row is the group's earliest frame, with **Frames** (how many it stands for) and **Integration** (their total exposure); the status bar and tab title count files and groups. Double-click a row, or use **Show the frames of this group** on its right-click menu, to open its frames in a new tab. Actions on selected rows (export, add to project, compress, plate solve, analyse, mark as bad) work on all the frames of the selected groups; viewing and the header show the row's frame. The search's other filters still apply: a group counts only the frames they let through. Reports always count every image

### Metadata Filters
- **Exposure** - Filter files by exposure time
- **Coordinates** - Filter files by celestial coordinates (RA/Dec)
- **Date** - Filter files by a capture date and time range, in **Local** time (the default, as the grid shows it) or in **UTC** (`DATE-OBS`). Prefilled from the selected row
- **Night** - Show one observing night, prefilled with the selected row's night (or else last night). A night runs from noon to noon in local time, so it includes the frames taken after midnight. It can be combined with the Date filter
- **Telescope** - Filter files by telescope used
- **Binning** - Filter files by camera binning settings
- **Gain** - Filter files by camera gain settings
- **Temperature** - Filter files by camera temperature
- **Quality Diagnosis** - Hide frames by their stored diagnosis against peer frames (the same night, target and equipment). Everything except the Bad values is ticked by default. A frame is hidden if any of its values is unticked. Unticking Not analysed gives a strict OK-only view

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

### Quality
- **Diagnose Quality…** - Judges the plain LIGHT frames of the current selection, or of the current search if nothing is selected, against their peers: the same night, target, filter, exposure, gain, binning, camera and telescope. Each metric can be off, or limited to X times the median absolute deviation or X% from the median, and the rejected counts update as you change them. **Analyse missing** analyses the frames in the scope that have no statistics, skipping groups too small to judge. Nothing is stored until you save, and **Save and hide bad frames** also adds the Quality Diagnosis filter. A selection covers the whole peer groups of the selected frames, because a frame can only be judged against its peers. The dialog's scope stays fixed until you ask for the current search or selection. It's also on the grid's right-click menu, and offered when Analyse Images finishes

### Plate Solving
- **Plate Solve…** - Plate solves the selected files, or the current search's unsolved light frames, with ASTAP, the Astrometry.net service, solve-field (see [Plate Solving with solve-field on WSL](solve-field-wsl.md)) or Seti Astro Suite Pro's Gaia solver (see [Plate Solving with Seti Astro Suite Pro](saspro-solver.md)), optionally with a second solver as backup. **Only one frame per target per night** (on by default when the grid is grouped by night) solves just the earliest unsolved frame of each group and skips groups that already have a solved frame; **Retry Failed** then tries a failed group's next frame, up to three. The other frames stay unsolved, and the Catalog Report counts them through the solved one

### Calibration Frame Matching
- **Find matching darks** - Finds dark frames that match the selected light frames (disabled when no file is selected)
- **Find matching flats** - Finds flat frames that match the selected light frames (disabled when no file is selected)

## Report Menu

The Report menu provides various reporting and analysis tools.

### File Reports
- **List Files** - Generates a simple list of files matching current search criteria
- **Metadata Report** - Creates a detailed report of file metadata

### Aggregate Reports
- **Target Report** - Total integration time and number of observing nights per target, grouped by filter, telescope and camera
- **Catalog Report** - Which catalog objects your plate-solved images cover. A solved frame also covers the other frames of its group (the same target on the same night, with the same image type, folder, filter and exposure, as Group by Night shows them), so solving one frame per group is enough: the others are listed in grey italics, with a tooltip naming the solved frame. Besides the built-in catalogs you can use your own, see [Custom catalogs](#custom-catalogs)

### External Integration
- **Telescopius Target List** - Compares one of your Telescopius observing lists with the current search and shows which targets have already been imaged. Requires a Telescopius API key (Settings > Telescopius)

The Target Report, the Catalog Report and the Metadata Report's field extraction are also
available to AI agents over MCP — see [mcp.md](mcp.md).

#### Custom catalogs

The **Custom** button next to the catalog selector in the Catalog Report lets you bring
your own catalog, for example a list of targets you plan to image:

- **Import CSV…** asks for a CSV file and a name for the catalog (the file name by
  default), then adds it to the catalog selector. Importing again under the same name
  replaces that catalog. The names of the built-in catalogs can't be used.
- **Remove "*name*"…** deletes the selected custom catalog. It is only available for
  catalogs you imported.

Custom catalogs are stored in the library, so each library has its own. Besides the
Catalog Report, they appear in image annotations, the object lookup and over MCP.

The CSV file needs a header row. Columns are separated by commas or semicolons, and their
names are not case sensitive:

| Column | Required | Meaning |
|---|---|---|
| `id` (or `name`) | yes | The object's identifier within your catalog |
| `ra` | yes | Right ascension: decimal degrees (`83.82`), or hours as `05:35:17.3`, `05 35 17.3` or `05h35m17.3s` |
| `dec` | yes | Declination: decimal degrees (`-5.39`), or `-05:23:28`, `-05 23 28` or `-05d23m28s` |
| `magnitude` | no | Brightness, shown in the report |
| `size` | no | Size in arcminutes, shown in the report |
| `axis_ratio`, `angle` | no | Shape, used to draw the object in image annotations |
| `canonical_id` | no | Another name for the object, e.g. `NGC1976` for `M42` |

Example:

```csv
name;ra;dec;magnitude;size
M42;05:35:17.3;-05:23:28;4.0;85
Heart Nebula;02:33:22;+61:26:36;6.5;150
Spaghetti Nebula;85.25;27.97;;180
```

Rows that can't be read (an unreadable coordinate, a missing id, or an id that appears
twice) are skipped, and the import tells you which lines were skipped and why. A file
with no `id`, `ra` or `dec` column, or with no usable row at all, is not imported.

## Help Menu

The Help menu provides access to application information and logging.

### Information and Support
- **View History** - Opens the application log window to view system messages and errors
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