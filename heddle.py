"""Pack complete x86-64 files, keeping a selected code range in field streams."""
import argparse
import lzma
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

import opcodes
import split

LZMA = dict(id=lzma.FILTER_LZMA1, dict_size=1 << 23, nice_len=96)


# Encoder-only choices for a solid core image. The raw LZMA properties
# are the same as the default member profile.
CORE_LZMA = dict(id=lzma.FILTER_LZMA1, preset=9 | lzma.PRESET_EXTREME,
                 dict_size=1 << 21, mf=lzma.MF_BT3, depth=256, nice_len=64)


def code_bounds(data):
    """File offsets of .text in a little-endian x86-64 ELF or PE."""
    def read(fmt, off):
        return struct.unpack_from("<" + fmt, data, off)

    if data.startswith(b"\x7fELF\x02\x01") and read("H", 18)[0] == 62:
        table, = read("Q", 40)
        step, count, names = read("HHH", 58)
        if step < 64 or names >= count or table + step * count > len(data):
            raise ValueError("invalid ELF section table")
        start, length = read("QQ", table + step * names + 24)
        strings = data[start:start + length]
        for i in range(count):
            entry = table + i * step
            name, kind, flags = read("IIQ", entry)
            if strings[name:name + 6] == b".text\0" and kind == 1 and flags & 4:
                offset, size = read("QQ", entry + 24)
                break
        else:
            raise ValueError("missing executable .text")
    elif data.startswith(b"MZ"):
        pe, = read("I", 60)
        if data[pe:pe + 6] != b"PE\0\0\x64\x86":
            raise ValueError("expected x86-64 PE")
        count, = read("H", pe + 6)
        optional, = read("H", pe + 20)
        for i in range(count):
            entry = pe + 24 + optional + i * 40
            if data[entry:entry + 8] == b".text\0\0\0":
                virtual, _, stored, offset = read("IIII", entry + 8)
                size = min(virtual, stored)
                break
        else:
            raise ValueError("missing PE .text")
    else:
        raise ValueError("supply --code LO HI for a raw image")
    if offset + size > len(data):
        raise ValueError("code runs past end of file")
    return offset, offset + size


def compress(data, filters=None):
    return lzma.compress(data, format=lzma.FORMAT_RAW, filters=filters or [LZMA])


# Independent LZMA contexts for small operands, larger immediates and
# targets, opcodes, and verbatim data. Each block describes its own bounds.
GROUPS = ((9, 0, 0, 2), (18, 1, 2, 2), (19, 3, 0, 0), (None, 0, 1, 2))


def jcc_xor(fields, inverse=False):
    lengths = struct.unpack_from(f"<{split.NS}I", fields, 8)
    index = split.ORDER.index(split.JCC)
    start = 8 + 4 * split.NS + sum(lengths[:index])
    length = lengths[index]
    if length % 4 or start + length > len(fields):
        raise ValueError("invalid JCC stream")
    data = bytearray(fields)
    prev = 0
    for pos in range(start, start + length, 4):
        value, = struct.unpack_from("<I", data, pos)
        struct.pack_into("<I", data, pos, value ^ prev)
        prev = value ^ prev if inverse else value
    return data


def grouped(fields, profile, size, tokens):
    lengths = struct.unpack_from(f"<{split.NS}I", fields, 8)
    head = 8 + 4 * split.NS
    payload = bytearray()
    used_xor = False
    start = 0
    for stop, lc, lp, pb in GROUPS:
        end = len(fields) if stop is None else head + sum(lengths[:stop])
        if start < end:
            filters = [dict(profile, lc=lc, lp=lp, pb=pb)]
            block = compress(fields[start:end], filters)
            jcc_index = split.ORDER.index(split.JCC)
            jcc = head + sum(lengths[:jcc_index])
            if start <= jcc < end and lengths[jcc_index]:
                other = compress(jcc_xor(fields)[start:end], filters)
                if len(other) < len(block):
                    used_xor, block = True, other
            props = (pb * 5 + lp) * 9 + lc
            if stop == split.OP + 1 and tokens:
                encoded = opcodes.encode(tokens)
                if 0 < len(encoded) <= size:
                    candidate = struct.pack("<I", len(encoded)) + compress(encoded, filters)
                    if len(candidate) < len(block):
                        props, block = 253, candidate
            payload += struct.pack("<BII", props, len(block), end - start)
            payload += block
        start = end
    return bytes(payload), used_xor


def member(fields, size, profile=LZMA, tokens=None):
    payload = compress(fields, [profile])
    magic = b"HDS2"
    # Very small members cannot recover the block headers.
    if len(fields) >= 1024:
        blocks, used_xor = grouped(fields, profile, size, tokens)
        if len(blocks) < len(payload):
            payload = blocks
            magic = b"HDX2" if used_xor else b"HDB2"
    header = struct.pack("<4sII", magic, size, len(fields))
    return header + struct.pack("<I", zlib.crc32(payload, zlib.crc32(header))) + payload


def pack(data, bounds=None, profile=LZMA):
    tokens = []
    fields = split.split(data, *(bounds if bounds is not None else code_bounds(data)), tokens=tokens)
    return member(fields, len(data), profile, tokens)


def expand(payload, need, profile):
    """A stream is whole when liblzma reads it to its end marker, without
    error, as exactly need bytes, with no byte of payload left over."""
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[profile])
    try:
        fields = decoder.decompress(payload, max_length=need + 1)
    except lzma.LZMAError as exc:
        raise ValueError(f"invalid compressed stream: {exc}") from None
    if len(fields) != need or not decoder.eof or decoder.unused_data:
        raise ValueError("invalid compressed length")
    return fields


def unpack(data, limit=256 << 20):
    magic, size, need, crc = struct.unpack_from("<4sIII", data)
    payload = data[16:]
    if magic not in (b"HDL1", b"HDS2", b"HDB2", b"HDX2") or max(size, need) > limit:
        raise ValueError("invalid header or size limit exceeded")
    if zlib.crc32(payload, zlib.crc32(data[:12])) != crc:
        raise ValueError("checksum mismatch")
    legacy = magic == b"HDL1"
    if magic in (b"HDL1", b"HDS2"):
        return split.join(expand(payload, need, LZMA), size, legacy)
    fields = bytearray()
    pos = 0
    while pos < len(payload):
        props, packed, raw = struct.unpack_from("<BII", payload, pos)
        pb, rem = divmod(props, 45)
        lp, lc = divmod(rem, 9)
        pos += 9
        if raw > need - len(fields) or packed > len(payload) - pos:
            raise ValueError("invalid block")
        if props == 253:
            encoded, = struct.unpack_from("<I", payload, pos)
            if packed < 4 or encoded > size:
                raise ValueError("invalid opcode block")
            tokens = expand(payload[pos+4:pos+packed], encoded, dict(LZMA, lc=3, lp=0, pb=0))
            fields.extend(opcodes.decode(tokens, raw))
        else:
            if pb > 4 or lp + lc > 3:
                raise ValueError("invalid block properties")
            fields.extend(expand(payload[pos:pos+packed], raw, dict(LZMA, lc=lc, lp=lp, pb=pb)))
        pos += packed
    if len(fields) != need:
        raise ValueError("incomplete blocks")
    if magic == b"HDX2":
        fields = jcc_xor(fields, inverse=True)
    return split.join(fields, size, legacy)


def compare(data, bounds=None):
    tokens = []
    fields = split.split(data, *(bounds if bounds is not None else code_bounds(data)), tokens=tokens)
    bcj2 = None
    seven = shutil.which("7z") or shutil.which("7zz")
    if seven:
        with tempfile.TemporaryDirectory() as tmp:
            coder = "LZMA:d=8m:fb=96"
            subprocess.run([seven, "a", "-m0=BCJ2", "-m1=" + coder, "-m2=" + coder,
                            "-m3=" + coder, "-mb0:1", "-mb0s1:2", "-mb0s2:3",
                            "-sibin", "a.7z"], cwd=tmp, input=data, check=True,
                           stdout=subprocess.DEVNULL)
            listing = subprocess.check_output([seven, "l", "-slt", "a.7z"], cwd=tmp,
                                              env=dict(os.environ, LC_ALL="C"), text=True)
            bcj2 = int(re.search(r"^Packed Size = (\d+)", listing, re.M)[1])
    return (len(data), len(compress(data)), len(compress(data, [dict(id=lzma.FILTER_X86), LZMA])),
            bcj2, len(member(fields, len(data), tokens=tokens)) - 16)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest="cmd", required=True)
    for name in ("pack", "unpack", "compare"):
        cmd = cmds.add_parser(name)
        cmd.add_argument("input", type=Path)
        if name != "compare":
            cmd.add_argument("output", type=Path)
        if name != "unpack":
            cmd.add_argument("--code", nargs=2, type=lambda s: int(s, 0), metavar=("LO", "HI"))
    args = parser.parse_args()
    try:
        data = args.input.read_bytes()
        if args.cmd == "compare":
            print("input\tLZMA\tx86 BCJ\tBCJ2\theddle")
            print(*("n/a" if n is None else n for n in compare(data, args.code)), sep="\t")
        else:
            args.output.write_bytes(unpack(data) if args.cmd == "unpack" else pack(data, args.code))
    except (ValueError, OSError, struct.error, lzma.LZMAError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"heddle: {exc}\n")
