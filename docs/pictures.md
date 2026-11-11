# JPEG and PNG Pictures

Besides FITS and XISF data, PhotonFinder can list the JPEG and PNG pictures you keep next to it: processed results, annotated exports, a DSLR or phone shot of the same target. They are reference pictures, not measurement data, so PhotonFinder treats them more simply than your frames.

## Turning it on

Pictures are imported per library root, and only when you ask for them:

1. Open **File > Manage Libraries**, and add a root or edit one.
2. Tick **Include pictures (.jpg, .jpeg, .png)**. The extensions are matched in any case (`.JPG` too).
3. Scan the library.

Roots start with pictures switched off, including the roots you had before this option existed.

**Switching it off removes them.** When you untick the box on a root that has pictures, PhotonFinder asks first. At the next scan the root's pictures leave the library, along with their project links and plate solutions. The files on disk are never touched.

## What PhotonFinder knows about a picture

A picture has the image type **PICTURE**, and PhotonFinder knows only its **file name, folder and size** (width and height). Nothing else is read from it:

- **No EXIF** or other embedded metadata: no camera, exposure, object name or position.
- **No date.** A picture doesn't match a date or observing-night search, and it sorts with the undated frames.
- **No header.** Show metadata is disabled for a picture.

You can find pictures by name, folder, project and type: choose **PICTURE** in the type filter. Pictures never mix with your frames in calibration matching, integration reports or quality diagnosis, as those work by image type.

## Viewing

The viewer and the thumbnail view show a picture **as it is**, in its own colours, and never stretch or debayer it. While a picture is shown, the stretch and debayer controls are disabled. They keep their settings, a locked stretch included, for the next image.

The picture's own pixels are shown exactly as stored. An EXIF rotation, as phones write, is ignored, so a picture saved "rotated" shows unrotated. That keeps a plate solution valid for the picture.

## Plate solving

Select one or more pictures and use **Plate Solve** as for any frame. Every solver reads JPEG and PNG directly: ASTAP, solve-field on WSL, Seti Astro Suite Pro, and Astrometry.net online, which receives the picture itself as the upload.

- Pictures are solved **blind**: a picture carries no position or scale, and the dialog's hints aren't used for it.
- Each picture is **solved on its own**. With **Group Session Frames**, every picture is a group of one, as one plate solution fits one picture only.
- Nothing is written next to your picture: the solvers' working files stay in a temporary folder.
- Once solved, the picture shows in sky searches and the viewer can **annotate** it with catalog objects.

A heavily compressed JPEG can fail to solve where the same image as a PNG solves; Seti Astro Suite Pro is the most sensitive to this. A failed solve is reported like any other, and the backup solver, if you set one, gets its turn.

## Projects and export

- Pictures can be added to projects like any file, and are listed in the project editor.
- **Export** copies a picture exactly as it is, under its own extension. Custom headers and the plate-solution override don't apply to it.

## What doesn't apply

These features need FITS or XISF data, so they leave pictures out:

- **Compress files** is disabled for a picture, and a mixed selection compresses only its FITS files.
- **Analyse Images** is disabled for a selection of only pictures. In a mixed selection, or a search, pictures are skipped and the log says how many.
- **Find matching darks/flats** isn't offered for a picture.

## MCP

To an MCP client, a picture is a file of type `PICTURE`. `get_file_details` returns no header for it, and a `header_note` saying why.
