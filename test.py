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


def frame(payload, n, scratch):
    h = struct.pack("<4sII", b"HDL1", n, scratch)
    return h + struct.pack("<I", zlib.crc32(payload, zlib.crc32(h))) + payload


fields = split.split(data, 3, 250)
for i in range(20):
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
