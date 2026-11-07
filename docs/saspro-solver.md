# Plate Solving with Seti Astro Suite Pro

[Seti Astro Suite Pro](https://www.setiastro.com) (SASpro) has its own plate solver, built on the
Gaia DR3 star catalogue. PhotonFinder can use it as its main solver, or as the backup when another
solver fails. It solves offline and needs no API key. It works out where an image points from its
star pattern alone, so it also solves frames with missing or wrong coordinates in their header.

It handles some images the other solvers struggle with: dense star fields, and stretched or edited
images rather than raw linear frames.

> Checked on 2026-11-07 with SASpro 1.23.8 on Windows 11.

## 1. Install SASpro

Download and install the latest version from [setiastro.com](https://www.setiastro.com). The
installer puts it in `C:\Program Files\SetiAstroSuitePro`, which is where PhotonFinder looks by
default.

## 2. Build the quad index

The solver needs a quad index, built in SASpro from its Gaia library. This is a one-time step, done
in SASpro itself. Seti Astro has a [video on building the quad dictionaries](https://youtu.be/MLroe8W_Zh4).

The index is made of layers, each for a range of field sizes. Build the layers that cover your
fields of view. For example, a 1.9° × 1.3° field is covered by the layers for 1–4° fields. The index
is stored in `%APPDATA%\SetiAstro\Seti Astro Suite Pro\gaia_quad_library`.

## 3. Set it up in PhotonFinder

Open **File → Settings → Plate Solving**. If SASpro is in its standard folder, leave **SASpro Path**
empty. Otherwise, set it to your `SetiAstroSuitePro.exe` with **Browse...**.

Then, in **Plate Solve…**, choose **SASpro (Gaia)** as the primary or the backup solver. For an AI
agent using PhotonFinder's [MCP server](mcp.md), the solver is called `saspro`.

## What PhotonFinder does

For each image, PhotonFinder runs SASpro's command-line solver (`SetiAstroSuitePro.exe solve`)
without opening the SASpro window:

- **Only SASpro's own Gaia solver is used.** SASpro can fall back to ASTAP or Astrometry.net by
  itself. PhotonFinder turns that off, so a result labelled SASpro really comes from SASpro. Use
  PhotonFinder's backup solver setting for fallback instead.
- **SASpro gets a temporary FITS file with only the image data.** That way it works the same for
  FITS, compressed FITS and XISF files, and your files are never changed. SASpro misreads some FITS
  headers, which makes it read the image upside down. Leaving the header out avoids that, and
  PhotonFinder rejects any result where SASpro reports reading the image that way.
- **The position and pixel scale from the header (or your hint) speed up the solve.** If that
  solve fails, for example because the header's coordinates are wrong, PhotonFinder solves the
  image again without them.

A blind solve usually takes 6–9 seconds per image.

### SASproSolver.exe is not needed

Seti Astro also offers `SASproSolver.exe`, which lets N.I.N.A. use SASpro by pretending to be ASTAP
or another solver. PhotonFinder doesn't use it, because it calls SASpro directly. Its stand-in
modes also let SASpro fall back to ASTAP, and the results some of them return can't be stored as a
full solution.

## Troubleshooting

- **"SASpro could not be started"**: the path in Settings is wrong, or SASpro isn't installed.
  Check **SASpro Path**, or leave it empty for the standard folder.
- **Every image fails with "blind solve found no match"**: the quad index is probably missing the
  layers for your field size. Check which layers are built in SASpro, then build the missing ones.
- **"match quality too low"** on the first attempt, followed by a blind solve: the header's
  coordinates were wrong. This is expected; the blind retry handles it.
- **SASpro's own log** is at `%APPDATA%\SetiAstroSuitePro\logs\saspro.log`. PhotonFinder's
  plate-solve output also shows SASpro's reason for each failure.
