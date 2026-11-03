# Plate Solving with solve-field on WSL

solve-field is the plate solver from [Astrometry.net](https://astrometry.net), the same engine the
online nova.astrometry.net service runs. Installed in Ubuntu under the Windows Subsystem for Linux
(WSL), it solves offline, needs no API key, and PhotonFinder can use it as its main solver or as the
backup when ASTAP fails.

This guide takes a Windows PC from nothing to a working solve-field in PhotonFinder.

> Checked on 2026-11-03 with Windows 11, WSL 2.6.3, Ubuntu 26.04.1 LTS (what `wsl --install`
> installs), `astrometry.net` 0.98, and the index packages `astrometry-data-tycho2` 2-5build1 and
> `astrometry-data-2mass-*` 1.2build1. Package names and sizes may differ on other Ubuntu releases.

## 1. Install WSL and Ubuntu

Open **PowerShell** and run:

```powershell
wsl --install
```

If WSL isn't on your PC yet, this installs it together with Ubuntu, and Windows may ask you to
restart. Microsoft's [WSL installation guide](https://learn.microsoft.com/windows/wsl/install) covers
the details and older versions of Windows. If you already use WSL with another distribution,
`wsl --install Ubuntu` adds Ubuntu next to it.

The first time Ubuntu starts, it asks you to choose a user name and password for Linux. You will
need that password for the `sudo` commands below.

Check that Ubuntu runs under WSL 2:

```powershell
wsl -l -v
```

```
  NAME      STATE           VERSION
* Ubuntu    Stopped         2
```

The `*` marks your default distribution. Keep its name in mind for step 5.

## 2. Install solve-field

Open **Ubuntu** from the Start menu, or type `wsl` in PowerShell, and run:

```bash
sudo apt update
sudo apt install astrometry.net
```

Check that it's there:

```bash
command -v solve-field
```

This should print `/usr/bin/solve-field`.

## 3. Install index files

solve-field recognises the sky by comparing patterns of stars in your image with **index files**.
They aren't installed with solve-field, and they decide which images it can solve. An index file
only works for patterns within a narrow range of sizes, so which ones you need depends on your
**field of view**.

### Work out your field of view

You need your camera's pixel size (µm), its image width in pixels, and your focal length (mm),
including any reducer or Barlow:

- **Pixel scale** (arcsec per pixel) = 206.265 × pixel size ÷ focal length
- **Field width** (arcminutes) = pixel scale × image width ÷ 60

Example: a 3.76 µm camera, 5496 pixels wide, on a 625 mm refractor gives 206.265 × 3.76 ÷ 625 =
1.24″/px, and 1.24 × 5496 ÷ 60 = 114′, a field about 1.9° wide.

PhotonFinder's Plate Solve dialog shows the scale it infers from your files under **Hints**, which is
a quick way to get the pixel scale.

### Choose index packages

Astrometry.net advises index files whose patterns are **10% to 100% of your field width**. In
practice the larger patterns do most of the work: covering from about a third of the field width up
to the full width is usually enough, and smaller ones can be added if images fail to solve.

Ubuntu packages the index files in two families:

| Package | Pattern sizes | Disk space | Notes |
|---|---|---|---|
| `astrometry-data-tycho2-07` | 22′–30′ | 137 MB | Tycho-2 series: the data is in the package |
| `astrometry-data-tycho2-08` | 30′–44′ | 77 MB | |
| `astrometry-data-tycho2-09` | 44′–60′ | 40 MB | |
| `astrometry-data-tycho2-10-19` | 60′–2000′ | 37 MB | |
| **`astrometry-data-tycho2`** | **22′–2000′** | **290 MB** | **all four above** |
| `astrometry-data-2mass-07` | 22′–30′ | 162 MB | 2MASS series: downloads the data from data.astrometry.net while installing |
| `astrometry-data-2mass-06` | 16′–22′ | 321 MB | |
| `astrometry-data-2mass-05` | 11′–16′ | 644 MB | |
| `astrometry-data-2mass-04` | 8′–11′ | 1.3 GB | |
| `astrometry-data-2mass-03` | 5.6′–8′ | 2.6 GB | |
| `astrometry-data-2mass-02` | 4′–5.6′ | 4.9 GB | |
| `astrometry-data-2mass-01` | 2.8′–4′ | 9.0 GB | |
| `astrometry-data-2mass-00` | 2′–2.8′ | 13.9 GB | |

The two families overlap from 22′ upwards (`astrometry-data-2mass-07` and `-08-19` duplicate what
Tycho-2 covers), so use Tycho-2 for the large patterns and 2MASS only for the small ones. Avoid the
`astrometry-data-2mass` package that pulls in all of 2MASS: it downloads about 33 GB.

**What to install, by field width:**

- **Wider than about 1.5°** (camera lenses, short refractors): `astrometry-data-tycho2` is enough.
  The 1.9° example above solves blind with it in a few seconds.
- **About 30′ to 1.5°** (most refractors and small reflectors): `astrometry-data-tycho2` plus
  `astrometry-data-2mass-05`, `-06` and `-07`, about 1.4 GB in all.
- **About 15′ to 30′** (long focal lengths): add `astrometry-data-2mass-03` and `-04` as well.
  A 28′ field solved blind with `-06` and `-07` in testing.
- **Narrower than 15′**: you need the large 2MASS sets down to about a tenth of your field width, in
  the same way. Check the disk space first.

For example, for a field of about 1°:

```bash
sudo apt install astrometry-data-tycho2 astrometry-data-2mass-05 astrometry-data-2mass-06 astrometry-data-2mass-07
```

The 2MASS packages download their files while installing, so this needs an internet connection and
can sit on one package for a while. Everything goes to `/usr/share/astrometry`, where solve-field
already looks; no configuration is needed.

## 4. Test it from Windows

Before involving PhotonFinder, solve one of your own light frames from PowerShell. Windows drives
appear under `/mnt/` in WSL, so `C:\Astro\M106.fits` is `/mnt/c/Astro/M106.fits`:

```powershell
wsl solve-field --no-plots --new-fits none --downsample 2 --dir /tmp/solve-test /mnt/c/Astro/M106.fits
```

`--dir /tmp/solve-test` keeps solve-field's output files (`.wcs`, `.solved`, `.axy` and more) inside
Linux. Without it, they are written next to your image. A successful run ends with lines like:

```
Field 1: solved with index index-tycho2-08.littleendian.fits.
Field center: (RA H:M:S, Dec D:M:S) = (12:18:56.622, +47:18:49.126).
Field size: 1.89347 x 1.26527 degrees
```

If you see only `did not solve` lines, revisit step 3: the index files don't match your field of view.

Add `-d <name>` after `wsl` to use a distribution that isn't your default, for example
`wsl -d Ubuntu-24.04 solve-field …`.

## 5. Set up PhotonFinder

Open **File → Settings** and go to the **Plate Solving** tab:

- **solve-field Path:** leave it **empty**. Empty means "use WSL". A path here is only for the
  Windows (Cygwin) builds of solve-field that come with ANSVR or All Sky Plate Solver.
- **WSL Distro:** leave it empty to use your default distribution (the one marked `*` by
  `wsl -l -v`), or enter the name of the one you installed solve-field in.
- **solve-field CPU time limit (seconds):** how long solve-field may work on one image, 10 to 600
  seconds, 300 by default.

Then select some light frames and choose **Tools → Plate Solve…**. Choose **solve-field** as the
**Solver**, or as the **Backup solver** behind ASTAP, and press **Start**. The log in the dialog
shows the exact `wsl … solve-field …` command PhotonFinder runs, followed by solve-field's output.

PhotonFinder passes along what it knows about each file: the position from its header (searching
2° around it) and the pixel scale from its header or from the **Hints**. A good scale hint makes
solving much faster, especially when you have many index files installed.

## Troubleshooting

**"WSL could not translate …: There is no distribution with the supplied name."**
The **WSL Distro** setting doesn't match a distribution. Compare it with the names `wsl -l -v`
shows, or clear it to use the default.

**`solve-field: command not found` in the log**
solve-field isn't installed in the distribution PhotonFinder uses. If you have several, check which
one is the default (`*` in `wsl -l -v`) or set **WSL Distro**, then repeat step 2 there.

**"You must list at least one index in the config file"**
No index files are installed. See step 3.

**Many `did not solve (index …)` lines, then "solve-field could not solve …"**
The installed index files don't fit your field of view, or the scale hint is wrong. Work out your
field width (step 3) and check you have the matching packages. If a file's header has a wrong focal
length, enter the right scale under **Hints** and choose **Override**.

**It runs until the CPU time limit**
A blind search (no position, no scale) through many index files can take minutes. Give a scale hint,
or tick **Try other successful values from the same camera** in the Plate Solve dialog.

**The first solve after starting Windows takes a few seconds longer**
WSL starts its Linux virtual machine on first use (about 3 seconds in testing). That's normal.

**Stopping a solve**
Closing the Plate Solve dialog stops solve-field inside WSL as well. Files already solved are kept.
