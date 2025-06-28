
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