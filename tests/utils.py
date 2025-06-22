
def fix_embedded_header(header_str: str) -> bytes:
    result = ""
    for line in header_str.splitlines():
        adj = line.ljust(80, " ")
        result += adj

    blocks = len(result) // 2880
    rem = len(result) % 2880
    if rem > 0:
        blocks += 1
    return bytes(result.ljust(blocks * 2880, " "), "ascii")