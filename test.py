"""Host decoder tests. Optional corpus: FILE or FILE:LO:HI (file offsets)."""
import os
import random
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import heddle
import split

rng = random.Random(3801)
cases = bytearray()
count = 0


def case(member, n, scratch, outcome, expected=b""):
    global count
    cases.extend(struct.pack("<4I", len(member), n, scratch, outcome))
    cases.extend(member)
    cases.extend(expected)
    count += 1


def valid(data, bounds):
    member = heddle.pack(data, bounds)
    assert heddle.unpack(member) == data
    need, = struct.unpack_from("<I", member, 8)
    case(member, len(data), need, 0, data)
    return member, need


for name in sys.argv[1:] or ["/usr/bin/true", "/usr/bin/ls", "/usr/bin/sed", "/usr/bin/xz"]:
    path, *bounds = name.rsplit(":", 2)
    valid(Path(path).read_bytes(), tuple(int(i, 0) for i in bounds) or None)
for n in range(64):
    data = rng.randbytes(n)
    lo = rng.randrange(n + 1)
    valid(data, (lo, rng.randint(lo, n)))
valid(bytes([0x66]) * 4096, (0, 4096))

data = bytes(range(256))
member, need = valid(data, (3, 250))
case(member, len(data) - 1, need, 1)
case(member, len(data), need - 1, 1)
for i in range(len(member)):
    case(member[:i], len(data), need, 1)
    damaged = bytearray(member)
    damaged[i] ^= 1
    case(damaged, len(data), need, 1)


def frame(payload, n, scratch, magic=b"HDS2"):
    h = struct.pack("<4sII", magic, n, scratch)
    return h + struct.pack("<I", zlib.crc32(payload, zlib.crc32(h))) + payload


fields = split.split(data, 3, 250)
payload = heddle.compress(fields)
block = struct.pack("<BII", 93, len(payload), need) + payload
good = frame(block, len(data), need, b"HDB2")
assert heddle.unpack(good) == data
case(good, len(data), need, 0, data)
for broken in (block[:8], block[:-1], bytes([255]) + block[1:], block + block,
               struct.pack("<BII", 93, len(payload), need + 1) + payload):
    case(frame(broken, len(data), need, b"HDB2"), len(data), need, 1)
# A dictionary block expands into the destination first, then the split
# scratch. Recompute checksums so damaged tags reach the opcode inverse.
def dictionary_block(tokens, raw):
    packed = heddle.compress(tokens, [dict(heddle.LZMA, pb=0)])
    return struct.pack("<BIII", 253, 4 + len(packed), raw, len(tokens)) + packed


for tokens in (b"", b"\0\0", b"\1\0", b"\1\0" + bytes(8) + b"\x01",
               b"\1\0" + bytes(8) + b"\x02", b"\1\0" + bytes(8) + b"\0\xff",
               b"\xff\0" + bytes(2040) + b"\0", b"\1\0\x08" + bytes(7) + b"\x01"):
    bad = dictionary_block(tokens, 1)
    case(frame(bad, 4096, 80, b"HDB2"), 4096, 80, 1)
# A hand-built v2 member with a one-entry dictionary of NOPs. Both
# blocks are needed for a complete round trip; changing the last tag to
# an absent entry preserves all framing and invalidates only the lookup.
nops = bytes([0x90]) * 128
fields_nop = split.split(nops, 0, len(nops))
prefix = fields_nop[:-len(nops)]
compressed = heddle.compress(prefix)
block_header = struct.pack("<BII", 93, len(compressed), len(prefix)) + compressed
# Each of 64 tags expands to two NOPs.
tokens = bytes.fromhex("0100 02 9090 0000000000") + bytes([1]) * 64
member_nop = frame(block_header + dictionary_block(tokens, 128), 128, len(fields_nop), b"HDB2")
assert heddle.unpack(member_nop) == nops
case(member_nop, 128, len(fields_nop), 0, nops)
broken = block_header + dictionary_block(tokens[:-1] + bytes([2]), 128)
case(frame(broken, 128, len(fields_nop), b"HDB2"), 128, len(fields_nop), 1)

# Legacy layout stores C7 immediates in I32 and has no MOV length word.
legacy_image = bytes.fromhex("c7 c0 78 56 34 12")
lengths = [0] * 18
lengths[split.I32], lengths[-1] = 4, 2
legacy = struct.pack("<20I", 0, 6, *lengths) + legacy_image[2:] + legacy_image[:2]
old = frame(heddle.compress(legacy), 6, len(legacy), b"HDL1")
assert heddle.unpack(old) == legacy_image
case(old, 6, len(legacy), 0, legacy_image)
for i in range(2 + split.NS):
    bad = bytearray(fields)
    struct.pack_into("<I", bad, 4*i, 0xffffffff)
    case(frame(heddle.compress(bad), len(data), need), len(data), need, 1)
for i in range(1000):
    # Bypass the checksum to exercise both decoders. Some mutations encode
    # valid alternative programs, so only the memory bounds are asserted.
    if i % 2:
        bad = bytearray(fields)
        pos = rng.randrange(20, len(bad))
        bad[pos] ^= 1 << rng.randrange(8)
        payload = heddle.compress(bad)
    else:
        payload = bytearray(member[16:])
        payload[rng.randrange(len(payload))] ^= rng.randint(1, 255)
    case(frame(payload, len(data), need), len(data), need, 2)
with tempfile.TemporaryDirectory() as tmp:
    exe = Path(tmp) / "test"
    subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-O1", "-g", "-DHEDDLE_CHECKED",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-Wall", "-Wextra",
                    "-Werror", "tests/native.c", "unpack.c", "unsplit.c", "-o", exe], check=True)
    subprocess.run([exe], input=cases, check=True)
print(f"{count} native decoder cases passed")
