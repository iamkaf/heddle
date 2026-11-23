"""Reversible x86-64 field filter. Both walkers must follow the same grammar."""
import os
import struct
import subprocess
import sys

NICE = 96

NS = 18                                 # the streams, in the order they are packed: the best
SIB, J8, D8, D8S, D8L, D32, D32S, D32L, RIP, I8, ISH, I32, IMR, I64, JCC, JMP, CALL, OP = range(NS)

PREFIX = (0x66, 0x67, 0xF2, 0xF3, 0xF0, 0x2E, 0x3E, 0x26, 0x64, 0x65, 0x36)

# What follows an opcode, as unsplit.c's nibbles: 8 a ModRM byte, plus
# 1 imm8, 2 imm16/32, 3 imm16/32/64, 4 rel8, 5 a call's rel32, 6 a jump's
# rel32, 7 TEST's immediate (F6, F7).
ONE = {}
for b in range(0x40):
    if b & 7 < 4:
        ONE[b] = 8
    elif b & 7 == 4:
        ONE[b] = 1
    elif b & 7 == 5:
        ONE[b] = 2
for b in (0x62, 0x63, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x8B, 0x8C, 0x8D, 0x8E,
          0x8F, 0xC4, 0xC5, 0xD0, 0xD1, 0xD2, 0xD3, 0xD8, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD,
          0xDE, 0xDF, 0xFE, 0xFF):
    ONE[b] = 8
for b, v in ((0x69, 10), (0x6B, 9), (0x80, 9), (0x81, 10), (0x83, 9), (0xC0, 9), (0xC1, 9),
             (0xC6, 9), (0xC7, 10), (0xF6, 15), (0xF7, 15), (0x68, 2), (0x6A, 1), (0xA8, 1),
             (0xA9, 2), (0xCD, 1), (0xE8, 5), (0xE9, 6), (0xEB, 4)):
    ONE[b] = v
for b in range(0x70, 0x80):
    ONE[b] = 4
for b in range(0xB0, 0xB8):
    ONE[b] = 1
for b in range(0xB8, 0xC0):
    ONE[b] = 3
for b in range(0xE0, 0xE8):
    ONE[b] = 1
TWO = {b: 8 for b in range(256)}
for b in (0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0E, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35,
          0x37, 0x77, 0xA0, 0xA1, 0xA2, 0xA8, 0xA9, 0xAA, *range(0xC8, 0xD0)):
    TWO[b] = 0
for b in range(0x80, 0x90):
    TWO[b] = 6
for b in (0x70, 0x71, 0x72, 0x73, 0xA4, 0xAC, 0xBA, 0xC2, 0xC4, 0xC5, 0xC6):
    TWO[b] = 9


def nibbles(t):
    return bytes(t.get(2 * i, 0) | t.get(2 * i + 1, 0) << 4 for i in range(128))


def walk(take):
    """One instruction, as unsplit.c reads it: take(stream, n) moves its
    next n bytes and gives the last. Returns where its call or RIP-relative
    field is from the instruction's start and its stream, (None, None) if
    it has none, and its length."""
    k, p66, w, at, s = 0, 0, 0, None, None

    def part(s, n):
        nonlocal k
        k += n
        return take(s, n)

    while True:
        op = part(OP, 1)
        if op not in PREFIX:
            break
        p66 |= op == 0x66
    if op & 0xF0 == 0x40:
        w = op & 8
        op = part(OP, 1)
    two = op == 0x0F
    if two:
        op = part(OP, 1)
        if op in (0x38, 0x3A):
            code = 9 if op == 0x3A else 8
            part(OP, 1)
        else:
            code = TWO[op]
    else:
        code = ONE.get(op, 0)
    m = 0
    if code & 8:
        m = part(OP, 1)
        mod, rm = m >> 6, m & 7
        if mod != 3:
            stack, lea = 0, op == 0x8D  # LEA's, and those off the stack pointer: apart
            if rm == 4:
                base = part(SIB, 1) & 7
                stack = base == 4
                if base == 5 and mod == 0:
                    part(D32, 4)
            elif mod == 0 and rm == 5:
                at, s = k, RIP
                part(RIP, 4)
            if mod == 1:
                part(D8L if lea else D8S if stack else D8, 1)
            elif mod == 2:
                part(D32L if lea else D32S if stack else D32, 4)
    kind = code & 7
    if kind == 7:
        kind = 0 if m >> 3 & 6 else 1 if op == 0xF6 else 2
    if kind == 1:                       # a shift's count apart
        part(ISH if op & 0xFE == 0xC0 else I8, 1)
    elif kind == 2:
        part(I32, 2 if p66 else 4)
    elif kind == 3:                     # MOV r, imm apart
        part(I64 if w else IMR, 8 if w else 2 if p66 else 4)
    elif kind == 4:
        part(J8, 1)
    elif kind == 5:
        at, s = k, CALL
        part(CALL, 4)
    elif kind == 6 and two:
        at, s = k, JCC
        part(JCC, 4)
    elif kind == 6:
        part(JMP, 4)
    return at, s, k


def split(mem, lo, hi):
    """mem's code, mem[lo:hi], split; the rest of it, its data, as it is."""
    mem = bytes(mem)
    if not 0 <= lo <= hi <= len(mem) <= 0xffffff00:
        raise ValueError("invalid image size or code bounds")
    size = hi - lo
    padded = mem[lo:hi] + bytes(64)     # past the code's end, the parse reads zeros
    streams = [bytearray() for _ in range(NS)]
    o, last = 0, [0] * NS

    def take(s, n):
        nonlocal o
        last[s] = len(streams[s])
        streams[s] += padded[o:o + n]
        o += n
        return padded[o - 1]

    while o < size:
        start = o
        at, s, n = walk(take)
        if at is not None and start + n <= size:
            v = struct.unpack_from("<I", padded, start + at)[0]
            struct.pack_into("<I", streams[s], last[s], (v + lo + start + n) & 0xFFFFFFFF)
    data = (struct.pack(f"<{NS + 2}I", lo, hi, *map(len, streams)) + b"".join(streams)
            + mem[:lo] + mem[hi:])
    if join(data, len(mem)) != mem:
        sys.exit("split: the streams do not make the program again")
    return data


def join(data, n):
    lo, hi, *lens = struct.unpack_from(f"<{NS + 2}I", data)
    if not 0 <= lo <= hi <= n <= 0xffffff00:
        raise ValueError("invalid code bounds")
    pos, ends, at = [], [], 4 * (NS + 2)
    for k in lens:
        pos.append(at)
        at += k
        ends.append(at)
    if at + n - (hi - lo) != len(data):
        raise ValueError("invalid stream lengths")
    raw = data[at:]
    out = bytearray(raw[:lo])

    def take(s, k):
        if pos[s] + k > ends[s]:
            raise ValueError("short instruction stream")
        out.extend(data[pos[s]:pos[s] + k])
        pos[s] += k
        return out[-1]

    while len(out) < hi:
        start = len(out)
        f, s, k = walk(take)
        if f is not None and start + k <= hi:
            v = struct.unpack_from("<I", out, start + f)[0]
            struct.pack_into("<I", out, start + f, (v - start - k) & 0xFFFFFFFF)
    if pos != ends:
        raise ValueError("unused instruction bytes")
    return bytes(out[:hi]) + raw[lo:lo + n - hi]


def check(stream, streams, mem, build="build"):
    """unpack.c and unsplit.c, compiled for this machine, on the packed
    stream: they must give mem, and leave
    their scratch all zeros. streams: what the stream unpacks to."""
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "unpacked.c")
    # The embedding build supplies its native round-trip checker beside this module.
    import tempfile
    with tempfile.TemporaryDirectory(dir=build) as tmp:
        tool = os.path.abspath(os.path.join(tmp, "unpacked"))
        subprocess.run(["gcc", "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror",
                        "-o", tool, src], check=True)
        r = subprocess.run([tool, str(len(mem)), str(streams)], input=stream,
                           capture_output=True)
    if r.returncode or r.stdout != bytes(mem):
        sys.exit("split: native decoder does not make the program again"
                 f" ({r.stderr.decode().strip() or 'other bytes'})")


def tables():
    def c(name, t):
        b = nibbles(t)
        rows = [", ".join(f"0x{x:02X}" for x in b[i:i + 12]) for i in range(0, 128, 12)]
        return f"static const uint8_t {name}[128] = {{\n    " + ",\n    ".join(rows) + "\n};"
    return c("one", ONE) + "\n" + c("two", TWO)


if __name__ == "__main__":
    print(tables())
