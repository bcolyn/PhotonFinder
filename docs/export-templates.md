# Export Path Templates

The export dialog uses Python [string.Template](https://docs.python.org/3/library/string.html#template-strings) syntax: variable names are written as `${name}`.

## File variables

| Variable | Description |
|---|---|
| `${filename}` | Plain filename. If decompressing, the compression extension (`.gz`, etc.) is dropped first. |
| `${filename_no_ext}` | Filename without the final extension. |
| `${ext}` | File extension without the leading dot (e.g. `fit`). |
| `${lib_path}` | Relative path within the library root. |
| `${last_light_path}` | Output path of the most recently exported LIGHT frame. Useful for placing calibration files next to their lights. |

## Image metadata variables

| Variable | Description |
|---|---|
| `${image_type}` | Frame type: `LIGHT`, `DARK`, `FLAT`, `BIAS`, etc. |
| `${camera}` | Camera name from the FITS header. |
| `${filter}` | Filter name. |
| `${exposure}` | Exposure time as a number (seconds). |
| `${gain}` | Capture gain. |
| `${binning}` | Binning (e.g. `1` for 1×1). |
| `${set_temp}` | Sensor target temperature. |
| `${telescope}` | Telescope name from the FITS header. |
| `${object_name}` | Target object name. |
| `${date_obs}` | Full capture timestamp in UTC ISO format. |
| `${date}` | Local capture date (`YYYY-MM-DD`), from the frame's local time (see below). |
| `${date_minus12}` | The observing night: the local capture time minus 12 hours, as a date. All frames of one night share it, whether taken before or after midnight. |
| `${sess_date}` | Night of the light-frame session this file belongs to. For light frames this is the same as `${date_minus12}`. For calibration frames (darks, flats, bias) it is the night of the lights they were matched to — which may differ from the calibration frame's own capture date. Falls back to `${date_minus12}` when no session context is available. |

### Local time

`${date}` and `${date_minus12}` use each frame's local time, taken from the first source its header has:

1. `DATE-LOC`, the local time the capture software recorded;
2. `DATE-OBS` in the time zone of the observatory (`SITELAT`/`SITELONG`, or `OBSGEO-B`/`OBSGEO-L`);
3. `DATE-OBS` in the timezone set under Settings → Observatory.

Releases before library version 3 used the UTC date of `DATE-OBS` for `${date}`, and UTC minus 12 hours for `${date_minus12}`. Re-exporting can therefore put some frames under another date folder: with `${date}`, frames taken between local midnight and the UTC date change; with `${date_minus12}`, frames far from UTC (in the Americas or Asia-Pacific, for example).

## Example patterns

Organise by night and filter:
```
${date_minus12}/${filter}/${filename}
```

Organise by object, then night:
```
${object_name}/${date_minus12}/${filename}
```

Place calibration files next to the light frames they belong to:
```
${last_light_path}/${filename}
```

## Shared calibration folder

When you export light frames, the export dialog finds calibration frames (flats, darks, bias,
dark flats) for each session. Several sessions often use the same calibration, for example one
set of darks for a whole month. With **Export the most-used calibration once, to a "shared"
folder, as the default** ticked, those frames are not copied into every session's folder:

- For each calibration type, the frames used by the **most** sessions are exported once, with the
  part of the pattern that holds `${sess_date}` replaced by `shared`. They are the default for every
  session.
- A session that uses **different** frames of that type keeps its own copy in its own session
  folder, which overrides the default.
- So there is at most one shared set per type. When two sets are used by equally many sessions,
  the set of the earliest session is shared, so exporting the same frames again gives the same result.
- The calibration table marks the frames that go to the shared folder with ☁.

For example, with the pattern `${sess_date}/${image_type}/${filename}`, the darks used by most
sessions end up in `shared/DARK/`, and a session with its own darks gets them in
`2024-03-12/DARK/`. The pattern needs `${sess_date}` for this to work: without it there is no
session folder to replace, and shared frames are written to the same place as the rest.
