
def fix_embedded_header(header_str: str) -> bytes:
    result = ""
    for line in header_str.strip().splitlines():
        adj = line.strip().ljust(80, " ")
        if adj:
            result += adj

    blocks = len(result) // 2880
    rem = len(result) % 2880
    if rem > 0:
        blocks += 1
    return bytes(result.ljust(blocks * 2880, " "), "ascii")


def single_header_scan(header_bytes: bytes):
    """What reading a single-image FITS file's headers gives (filesystem.read_fits_headers),
    for tests that stand in for the read."""
    from photonfinder.image_hdus import HduInfo, HduScan
    return HduScan(header_bytes, [HduInfo(0, None, (), header_bytes)])
