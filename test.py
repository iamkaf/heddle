"""Host decoder tests. Optional corpus: FILE or FILE:LO:HI (file offsets)."""
import lzma
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
sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
import lzma1

rng = random.Random(3801)
cases = bytearray()
count = 0


def case(member, n, scratch, outcome, expected=b""):
    global count
    cases.extend(struct.pack("<4I", len(member), n, scratch, outcome))
    cases.extend(member)
    cases.extend(expected)
    count += 1


def python(member):
    try:
        return heddle.unpack(member)
    except (ValueError, struct.error, lzma.LZMAError) as exc:
        return exc


def agree(member, expected=None, reason=None):
    """Both readers give the same verdict: expected, or a refusal (Python's
    naming reason). Capacities are the sizes the header states."""
    n, need = struct.unpack_from("<II", member, 4) if len(member) >= 12 else (64, 80)
    got = python(member)
    if expected is None:
        assert isinstance(got, Exception), "Python accepted a damaged member"
        assert reason is None or reason in str(got), (reason, got)
        case(member, n, need, 1)
    else:
        assert got == expected, got
        case(member, n, need, 0, expected)


def same(member):
    """Whatever Python makes of it, C must make the same."""
    got = python(member)
    agree(member, None if isinstance(got, Exception) else got)


def valid(data, bounds):
    member = heddle.pack(data, bounds)
    assert heddle.unpack(member) == data
    need, = struct.unpack_from("<I", member, 8)
    case(member, len(data), need, 0, data)
    if member[:3] != b"HDS":
        grouped.append(member)
    return member, need


grouped = []


for name in sys.argv[1:] or ["/usr/bin/true", "/usr/bin/ls", "/usr/bin/sed", "/usr/bin/xz"]:
    path, *bounds = name.rsplit(":", 2)
    valid(Path(path).read_bytes(), tuple(int(i, 0) for i in bounds) or None)
# A profile changes only the encoder's effort: settings the header cannot
# carry are refused before anything is written.
small = split.split(bytes(range(256)), 3, 250)
for profile in (heddle.LZMA, heddle.CORE_LZMA, dict(id=lzma.FILTER_LZMA1, preset=3),
                dict(heddle.CORE_LZMA, lc=3, lp=0, pb=2), dict(heddle.LZMA, dict_size=1 << 23)):
    assert heddle.unpack(heddle.member(small, 256, profile)) == bytes(range(256))
for profile, reason in ((dict(heddle.LZMA, lc=4), "lc/lp/pb"), (dict(heddle.LZMA, lp=1, lc=2), "lc/lp/pb"),
                        (dict(heddle.LZMA, pb=0), "lc/lp/pb"), (dict(heddle.LZMA, dict_size=(1 << 23) + 1), "8 MiB"),
                        (dict(id=lzma.FILTER_LZMA1, preset=9), "8 MiB"), (dict(heddle.LZMA, id=lzma.FILTER_LZMA2), "LZMA1")):
    try:
        heddle.member(small, 256, profile)
        raise AssertionError(f"wrote a member with {profile}")
    except ValueError as exc:
        assert reason in str(exc), exc
# Lengths the C API cannot take are refused at write time. Stand-in
# compressor output of the size in question, since real data that large
# would take minutes.
class Huge:
    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n

    def __radd__(self, other):
        return Huge(len(other) + self.n)


real_compress = heddle.compress
for fields_in, out in ((small, heddle.LIMIT - 15), (bytes(2048), heddle.LIMIT + 1)):
    heddle.compress = lambda data, filters=None: Huge(out)
    try:
        heddle.member(fields_in, 256)
        raise AssertionError("wrote a member over 4 GiB")
    except ValueError as exc:
        assert "4 GiB" in str(exc), exc
    finally:
        heddle.compress = real_compress
heddle.compress = lambda data, filters=None: Huge(heddle.LIMIT - 16)
try:                                    # the largest that fits gets as far as its checksum
    heddle.member(small, 256)
except TypeError:
    pass
finally:
    heddle.compress = real_compress
try:
    heddle.member(small, 0xFFFFFF01)
    raise AssertionError("wrote an image over 0xffffff00")
except ValueError as exc:
    assert "0xffffff00" in str(exc), exc
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


def blocks(member):
    """A grouped member's blocks: [props, stream, raw], the dictionary's
    intermediate size kept at the front of its stream."""
    out, pos = [], 16
    while pos < len(member):
        props, packed, raw = struct.unpack_from("<BII", member, pos)
        out.append([props, member[pos + 9:pos + 9 + packed], raw])
        pos += 9 + packed
    return out


def regroup(member, parts):
    n, need = struct.unpack_from("<II", member, 4)
    body = b"".join(struct.pack("<BII", props, len(stream), raw) + stream
                    for props, stream, raw in parts)
    return frame(body, n, need, member[:4])


# One rule for a whole LZMA stream, the same in both readers: first byte 0,
# exactly the stated bytes, distances inside the output and an 8 MiB
# window, an end marker of any length, the range coder's code then 0, and
# nothing after. Checksums are recomputed so only the stream is at fault.
def solid(stream, data):
    """An HDS2 member of data with no code; fields are 84 zero bytes, then data."""
    return frame(stream, len(data), 84 + len(data))


image = bytes(range(32)) * 2
fields0 = bytes(84) + image
agree(solid(lzma1.literals(fields0), image), image)
agree(solid(lzma1.literals(fields0, end=False), image), reason="length")
for length in (2, 9, 17, 273):
    e = lzma1.Encoder()
    for b in fields0:
        e.literal(b)
    e.end(length)
    agree(solid(e.finish(), image), image)
stream = lzma1.literals(fields0)
for bad, reason in ((b"\1" + stream[1:], "stream"), (stream + b"\0", "length"),
                    (stream[:-1], "length"), (stream[:-1] + bytes([stream[-1] ^ 1]), "stream")):
    agree(solid(bad, image), reason=reason)
e = lzma1.Encoder()                     # an end marker one byte early
for b in fields0[:-1]:
    e.literal(b)
e.end()
agree(solid(e.finish(), image), reason="length")
e = lzma1.Encoder()                     # a literal after the last byte
for b in fields0 + b"\0":
    e.literal(b)
e.end()
agree(solid(e.finish(), image), reason="length")
for tail in range(1, 4):                # a match running past the end
    e = lzma1.Encoder()
    for b in fields0[:-2]:
        e.literal(b)
    e.match(31, 2 + tail)
    e.end()
    agree(solid(e.finish(), image), reason="length")
for more in (lambda e: e.rep_match(0, 2), lambda e: e.short_rep()):
    e = lzma1.Encoder()                 # a rep, long or short, past the end
    for b in fields0:
        e.literal(b)
    more(e)
    e.end()
    agree(solid(e.finish(), image), reason="length")
e = lzma1.Encoder()                     # a bad distance where the marker goes
for b in fields0:
    e.literal(b)
e.match(len(fields0), 2)
agree(solid(e.finish(), image), reason="stream")
for d, ok in ((83 + len(image), True), (84 + len(image), False)):
    e = lzma1.Encoder()                 # the furthest distance, and one past
    for b in fields0:
        e.literal(b)
    e.match(d, 2)
    e.end()
    grown = image + fields0[-d - 1:][:2]
    agree(solid(e.finish(), grown), grown if ok else None)
window = 1 << 23
for d, ok in ((window - 1, True), (window, False)):
    e = lzma1.Encoder()                 # the 8 MiB window
    e.literal(0)
    while len(e.data) < window + 84:
        e.rep_match(0, 273)
    e.match(d, 2)
    e.end()
    zeros = bytes(len(e.data) - 84)
    agree(solid(e.finish(), zeros), zeros if ok else None)

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
# scratch. Each case below is a complete member whose framing a correct
# dictionary would satisfy, so a refusal can only come from the dictionary,
# and Python must refuse for the reason the case names.
def dictionary_block(tokens, raw):
    packed = heddle.compress(tokens, [dict(heddle.LZMA, pb=0)])
    return struct.pack("<BIII", 253, 4 + len(packed), raw, len(tokens)) + packed


# One-byte opcodes the walker takes alone, to spell distinct rows with.
BARE = [b for b in range(256) if not split.ONE.get(b) and b not in split.PREFIX
        and b & 0xF0 != 0x40 and b != 0x0F]


def form(i, width=3):
    return bytes(BARE[i // len(BARE) ** k % len(BARE)] for k in range(width))


def row(body, length=None):
    return bytes([len(body) if length is None else length]) + body.ljust(7, b"\0")


def table(rows, count=None):
    """The count, then each of the eight columns of the rows, delta coded."""
    data = bytearray(struct.pack("<H", len(rows) if count is None else count))
    for lane in range(8):
        column = [r[lane] for r in rows]
        data.extend(column[:1])
        data.extend((b - a) & 255 for a, b in zip(column, column[1:]))
    return bytes(data)


def tag(index, count):
    """What opcodes.encode writes for row index of count."""
    short = 255 - (count - 1) // 255
    if index < short:
        return bytes([index + 1])
    offset, page = divmod(index - short, 255 - short)
    return bytes([short + 1 + page, offset])


def dict_member(tokens, ops, magic=b"HDB2"):
    """An image of ops as code, padded with data so the intermediate fits."""
    pad = max(0, len(tokens) - len(ops))
    fields = split.split(ops + bytes(pad), 0, len(ops))
    head = fields[:84]
    blocks = struct.pack("<BII", 93, len(heddle.compress(head)), 84) + heddle.compress(head)
    blocks += dictionary_block(tokens, len(ops))
    if pad:
        tail = heddle.compress(bytes(pad), [dict(heddle.LZMA, lc=0, lp=1)])
        blocks += struct.pack("<BII", 99, len(tail), pad) + tail
    return frame(blocks, len(ops) + pad, len(fields), magic), ops + bytes(pad)


def dictionary(tokens, ops, reason=None):
    member, image = dict_member(tokens, ops)
    agree(member, None if reason else image, reason)


def lookup(tokens, index):
    """What a reader without the index and length checks would copy for a
    tag naming index: the decoders' own column walk, unbounded."""
    count, = struct.unpack_from("<H", tokens)
    columns = bytearray(tokens)
    for lane in range(8):
        for i in range(1, count):
            at = 2 + lane * count + i
            columns[at] = (columns[at] + columns[at - 1]) & 255
    return bytes(columns[2 + index + count * (j + 1)] for j in range(columns[2 + index]))


# A one-entry dictionary of NOPs: each of 64 tags expands to two.
nops = bytes([0x90]) * 128
dictionary(table([row(b"\x90\x90")]) + bytes([1]) * 64, nops)
# The first long tag at 256 entries, the last valid index and the first
# unused. Row 0 starts with 6, so index 256, read unchecked, is a valid
# six-byte row.
rows = [b"\x06\x90\x90"] + [form(i, 1 + i % 3) for i in range(1, 256)]
full = table([row(r) for r in rows])
picks = (0, 253, 254, 255)
dictionary(full + b"".join(tag(i, 256) for i in picks), b"".join(rows[i] for i in picks))
assert tag(254, 256) == b"\xff\0" and tag(255, 256) == b"\xff\1"
tokens = full + tag(0, 256) + b"\xff\2"
dictionary(tokens, rows[0] + lookup(tokens, 256), "invalid opcode index")
# Interleaved pages: at 1,000 entries, 252 short tags and three long ones
# whose second byte steps through the rows three at a time.
rows = [form(i) for i in range(1000)]
picks = (251, 252, 253, 254, 255, 256, 600, 998, 999)
assert [tag(i, 1000) for i in (252, 253, 255, 999)] == [b"\xfd\0", b"\xfe\0", b"\xfd\1", b"\xfd\xf9"]
full = table([row(r) for r in rows])
dictionary(full + b"".join(tag(i, 1000) for i in picks), b"".join(rows[i] for i in picks))
dictionary(full + b"\xfe\xf9", lookup(full, 1000), "invalid opcode index")
# The wire limit: 65,025 entries, one short tag and 254 pages of long ones;
# one more entry, with the tags that count would have, is refused.
rows = [form(i) for i in range(65026)]
picks = (0, 1, 254, 255, 32768, 65024)
assert tag(65024, 65025) == b"\xff\xff"
ops = b"".join(rows[i] for i in picks)
dictionary(table([row(r) for r in rows[:65025]]) + b"".join(tag(i, 65025) for i in picks), ops)
dictionary(table([row(r) for r in rows]) + b"".join(tag(i, 65026) for i in picks), ops, "invalid opcode dictionary")

# One defect each, in a member that would otherwise decode to ops. The
# tags outnumber the table, so the dictionary block is the member's last
# and an overrun reaches past the scratch.
rows = [form(i, 2) for i in range(3)]
base = table([row(r) for r in rows])
tags = bytes([1, 2, 3, 2]) * 8
ops = (rows[0] + rows[1] + rows[2] + rows[1]) * 8
assert len(base + tags) <= len(ops)
dictionary(base + tags, ops)
dictionary(b"\1", ops[:1], "invalid opcode dictionary")
dictionary(b"\0\0" + b"\0" + bytes([len(ops)]) + ops, ops, "invalid opcode dictionary")
dictionary(base[:-1], rows[0], "invalid opcode dictionary")  # the image is no longer than the block
dictionary(base + tags + b"\0", ops, "truncated opcode tag")
long_table = table([row(form(i)) for i in range(256)])
dictionary(long_table + tag(0, 256) * 260 + b"\xff", form(0) * 260, "truncated opcode tag")
short = bytes([1, 2, 3, 2]) * 6 + bytes([1, 2, 3])
tokens = base + short + b"\0\2" + rows[1][:1]
dictionary(tokens, ops[:2 * len(short)] + rows[1][:1] + b"\x90", "invalid opcode span")
assert len(tokens) == 2 * len(short) + 2      # so the byte after is past the image
dictionary(base + tags[:2] + b"\0\0" + tags[2:], ops, "invalid opcode span")
dictionary(table([row(r) for r in rows] + [row(b"", 0)]) + tags[:2] + b"\4" + tags[2:], ops, "invalid opcode span")
# An eight-byte row reads its last byte from the tags: tags[6], a 6 here,
# which is an opcode without operands.
seven = [form(i, 2) for i in range(6)]
tokens = table([row(r) for r in seven] + [row(form(9, 7), 8)]) + bytes([1, 2, 3, 4, 5, 6, 6]) + b"\7"
dictionary(tokens, b"".join(seven[t - 1] for t in (1, 2, 3, 4, 5, 6, 6)) + lookup(tokens, 6), "invalid opcode length")
dictionary(base + tags + b"\1", ops, "invalid opcode span")
dictionary(base + tags[:-1], ops, "incomplete opcode stream")
# Rows are checked when a tag uses them: an unused malformed row is
# accepted by both readers.
dictionary(table([row(r) for r in rows] + [row(b"", 9)]) + tags, ops)

# HDX2: successive distinct conditional targets, XORed in the scratch.
jumps = b"".join(b"\x0f\x84" + struct.pack("<i", 7 * i - 20) + b"\x90" for i in range(12))
fields_x = split.split(jumps, 0, len(jumps))
xored = bytes(heddle.jcc_xor(fields_x))
assert xored != fields_x
packed_x = heddle.compress(xored)
member_x = frame(struct.pack("<BII", 93, len(packed_x), len(xored)) + packed_x,
                 len(jumps), len(xored), b"HDX2")
agree(member_x, jumps)
same(b"HDB2" + member_x[4:12] + struct.pack("<I", zlib.crc32(member_x[16:], zlib.crc32(b"HDB2" + member_x[4:12])))
     + member_x[16:])

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
# The same rule inside each block of a grouped member, the dictionary's
# intermediate stream included.
assert grouped, "no grouped member to damage"
for whole in grouped[:2]:
    parts = blocks(whole)
    for j, (props, stream, raw) in enumerate(parts):
        at = 4 if props == 253 else 0
        for bad, reason in ((stream[:at] + b"\1" + stream[at + 1:], "stream"),
                            (stream + b"\0", "length"), (stream[:-1], "length")):
            agree(regroup(whole, parts[:j] + [[props, bad, raw]] + parts[j + 1:]), reason=reason)
# Damage that keeps the checksum good: both readers must agree on every
# mutation, accepting the same bytes or refusing.
for i in range(1000):
    if i % 2:
        bad = bytearray(fields)
        pos = rng.randrange(20, len(bad))
        bad[pos] ^= 1 << rng.randrange(8)
        payload = heddle.compress(bad)
    else:
        payload = bytearray(member[16:])
        payload[rng.randrange(len(payload))] ^= rng.randint(1, 255)
    same(frame(payload, len(data), need))
with tempfile.TemporaryDirectory() as tmp:
    exe = Path(tmp) / "test"
    subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-O1", "-g", "-DHEDDLE_CHECKED",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-Wall", "-Wextra",
                    "-Werror", "tests/native.c", "unpack.c", "unsplit.c", "-o", exe], check=True)
    subprocess.run([exe], input=cases, check=True)
print(f"{count} native decoder cases passed")
