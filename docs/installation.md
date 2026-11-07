# Installation Guide

<!-- The PhotonFinder installation steps themselves are still to be written. -->

## Optional: Plate Solvers

PhotonFinder doesn't need a plate solver to manage your files, but plate solving (finding where in
the sky each image points) needs at least one of these:

- **ASTAP**, installed on Windows. Set its path under **File → Settings → Plate Solving** tab.
- **Astrometry.net**, the online service. It needs an internet connection and an API key from
  [nova.astrometry.net](https://nova.astrometry.net), entered on the same tab.
- **solve-field**, the offline Astrometry.net solver, installed in Ubuntu under WSL. See
  [Plate Solving with solve-field on WSL](solve-field-wsl.md).
- **SASpro (Gaia)**, the plate solver built into [Seti Astro Suite Pro](https://www.setiastro.com).
  It solves offline, with or without a known position, from a Gaia DR3 quad index that you build
  in SASpro first (its plate-solve settings). PhotonFinder finds SASpro in its standard install
  folder; if you installed it elsewhere, set the path to `SetiAstroSuitePro.exe` on the same tab.
  It works out each image's position from its star pattern, so headers with wrong coordinates are
  no problem.
