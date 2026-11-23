"""The C packer (pack.c) held to the Python one. Optional corpus: FILE or
FILE:LO:HI (file offsets).

Builds pack.c with AddressSanitizer and UndefinedBehaviorSanitizer, and
again with liblzma when its header is installed. For every input, the C
member's fields equal the Python split's byte for byte, its opcode
dictionary (if it chose one) is opcodes.encode's, both readers read the
other packer's members, and the liblzma build's members are the Python
packer's byte for byte, given the same encoder setting (PROFILE)."""
import atexit
import lzma
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(here))
import heddle
import opcodes
import split

# The C packer's one encoder setting, as a Python profile.
PROFILE = dict(heddle.CORE_LZMA, dict_size=1 << 23)

cc = os.environ.get("CC", "cc")
tmp = Path(tempfile.mkdtemp())
atexit.register(shutil.rmtree, tmp)
flags = [cc, "-std=c11", "-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
# LZMA SDK 9.20 calls its match finder through function pointers whose
# parameter it casts to void *; clang's function check, which GCC has not,
# is left out for those three files alone.
sdk = flags + ["-fno-sanitize=function"] * ("clang" in Path(cc).name)
objects = []
for source in ("LzmaEnc.c", "LzFind.c", "Bra86.c"):
    objects.append(tmp / (source + ".o"))
    subprocess.run([*sdk, "-D_7ZIP_ST", "-c", here / "lzma" / source, "-o", objects[-1]], check=True)
ours = [*flags, "-Wall", "-Wextra", "-Werror", "-DHEDDLE_CHECKED", here / "pack.c", here / "unpack.c",
        here / "unsplit.c"]
packers = {"sdk": tmp / "heddle"}
subprocess.run([*ours, "-D_7ZIP_ST", *objects, "-o", packers["sdk"]], check=True)
if subprocess.run([*ours, "-DHEDDLE_LIBLZMA", objects[2], "-llzma", "-o", tmp / "heddle-xz"],
                  capture_output=True).returncode == 0:
    packers["liblzma"] = tmp / "heddle-xz"
else:
    print("liblzma: no header or library, so its build is not tested")


def run(packer, *args, ok=True):
    # A refusal exits at once: what it had allocated is not a leak.
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0") if not ok else None
    r = subprocess.run([packers[packer], *map(str, args)], capture_output=True, env=env)
    if ok and r.returncode:
        sys.exit(f"{packer} {' '.join(map(str, args))}: {r.stderr.decode()}")
    return r


def blocks(member):
    out, pos = [], 16
    while pos < len(member):
        props, packed, raw = struct.unpack_from("<BII", member, pos)
        out.append((props, member[pos + 9:pos + 9 + packed], raw))
        pos += 9 + packed
    return out


count = 0


def check(name, data, bounds=None):
    """bounds None: the packers find .text themselves."""
    global count
    image, out = tmp / "image", tmp / "out"
    image.write_bytes(data)
    code = ["--code", *bounds] if bounds is not None else []
    tokens = []
    fields = split.split(data, *(bounds if bounds is not None else heddle.code_bounds(data)), tokens=tokens)
    python = heddle.member(fields, len(data), heddle.LZMA, tokens)
    for packer in packers:
        run(packer, "pack", image, tmp / "member", *code)
        member = (tmp / "member").read_bytes()
        got, size, magic = heddle.expand_member(member)
        assert got == fields, f"{name}: {packer}'s fields are not the Python split's"
        assert heddle.unpack(member) == data, name
        dictionaries = [stream for props, stream, raw in blocks(member) if props == 253] \
            if magic in (b"HDB2", b"HDX2") else []
        assert name != "forms" or dictionaries, "the forms are not in a dictionary block"
        for stream in dictionaries:
            n, = struct.unpack_from("<I", stream)
            table = heddle.expand(stream[4:], n, dict(heddle.LZMA, pb=0))
            assert table == opcodes.encode(tokens), f"{name}: {packer}'s opcode dictionary"
        if packer == "liblzma":
            assert member == heddle.member(fields, len(data), PROFILE, tokens), f"{name}: not Python's member"
        for written in (member, python):          # the C reader, through the command
            (tmp / "member").write_bytes(written)
            run(packer, "unpack", tmp / "member", out)
            assert out.read_bytes() == data, name
    count += 1
    return member


for name in sys.argv[1:] or ["/usr/bin/true", "/usr/bin/ls", "/usr/bin/sed", "/usr/bin/xz"]:
    path, *bounds = name.rsplit(":", 2)
    check(name, Path(path).read_bytes(), tuple(int(i, 0) for i in bounds) or None)

rng = random.Random(3801)
for n in range(64):
    data = rng.randbytes(n)
    lo = rng.randrange(n + 1)
    check(f"random {n}", data, (lo, rng.randint(lo, n)))
check("prefixes", bytes([0x66]) * 4096, (0, 4096))
check("bytes", bytes(range(256)), (3, 250))

# More forms than the dictionary holds, with ties, long enough that the
# dictionary block is chosen: three prefixes, REX, a two-byte opcode with a
# ModRM byte and no operand, and a register ModRM, each two to four times.
TWO = [b for b in range(256) if split.TWO[b] == 8 and b not in (0x38, 0x3A)]
forms = [bytes([0x66, 0x67, 0x2E, 0x40 + i % 16, 0x0F, TWO[i // 16 % len(TWO)], 0xC0 + i // 16 // len(TWO)])
         for i in range(9000)]
assert len(set(forms)) == 9000
ops = [f for i, f in enumerate(forms) for _ in range(2 + i % 3)]
rng.shuffle(ops)
once = b"".join(b"\x3e" + f[1:] for f in forms[:100])   # 700 bytes of literals in a row
ops = b"".join(ops[:5000]) + once + b"".join(ops[5000:])
check("forms", ops, (0, len(ops)))

# A PE the packers find .text in, holding real code, and malformed headers
# both refuse with the same words.
def pe(text, machine=0x8664, name=b".text\0\0\0", sections=1):
    head = bytearray(64)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 60, 64)
    head += b"PE\0\0" + struct.pack("<HH12xHH", machine, sections, 0, 0x22)
    head += name + struct.pack("<IIII16x", len(text), 0x1000, len(text), 512)
    return bytes(head.ljust(512, b"\0")) + text


ls = Path("/usr/bin/ls").read_bytes()
lo, hi = heddle.code_bounds(ls)
text = ls[lo:lo + 32768]
check("PE", pe(text))
elf = bytearray(ls)
struct.pack_into("<H", elf, 62, 0xFFFF)
for data in (pe(text, machine=0x14C), pe(text, name=b".data\0\0\0"), pe(text, sections=0), bytes(elf),
             b"not a program"):
    try:
        heddle.code_bounds(data)
        raise AssertionError("Python found code")
    except ValueError as exc:
        reason = str(exc)
    (tmp / "image").write_bytes(data)
    for packer in packers:
        r = run(packer, "pack", tmp / "image", tmp / "member", ok=False)
        assert r.returncode == 1 and r.stderr.decode() == f"heddle: {reason}\n", (reason, r.stderr)
(tmp / "image").write_bytes(bytes(100))
for args in (("pack", tmp / "image", tmp / "member", "--code", 5, 101), ("pack", tmp / "image", tmp / "member", "--code", 9, 8)):
    r = run("sdk", *args, ok=False)
    assert r.returncode == 1 and b"invalid image size or code bounds" in r.stderr, r.stderr
for args in (("pack", tmp / "image"), ("unpack", tmp / "image", tmp / "out", "--code", 0, 1), ("mend",),
             ("pack", tmp / "image", tmp / "member", "--code", "0x", 1)):
    assert run("sdk", *args, ok=False).returncode == 2, args
run("sdk", "pack", tmp / "image", tmp / "member", "--code", 3, 50)
member = bytearray((tmp / "member").read_bytes())
member[20] ^= 1
(tmp / "member").write_bytes(member)
r = run("sdk", "unpack", tmp / "member", tmp / "out", ok=False)
assert r.returncode == 1 and r.stderr == b"heddle: invalid member\n", r.stderr

# Compare: the heddle column is the member's payload; with liblzma, the
# LZMA and x86 BCJ columns are liblzma's with the same setting, so the
# SDK's x86 converter is liblzma's. BCJ2 is 7-Zip's, as Python asks it.
data = Path("/usr/bin/ls").read_bytes()
python = heddle.compare(data)
for packer in packers:
    head, row = run(packer, "compare", "/usr/bin/ls").stdout.decode().splitlines()
    assert head == "input\tLZMA\tx86 BCJ\tBCJ2\theddle", head
    row = row.split("\t")
    run(packer, "pack", "/usr/bin/ls", tmp / "member")
    assert int(row[0]) == len(data) and int(row[4]) == len((tmp / "member").read_bytes()) - 16, row
    assert row[3] == str(python[3]) if python[3] is not None else row[3] == "n/a", (row, python)
    if packer == "liblzma":
        assert int(row[1]) == len(heddle.compress(data, [PROFILE])), row
        assert int(row[2]) == len(heddle.compress(data, [dict(id=lzma.FILTER_X86), PROFILE])), row
print(f"{count} inputs packed alike by {' and '.join(packers)}")
