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

import split

LZMA = dict(id=lzma.FILTER_LZMA1, dict_size=1 << 23, nice_len=96)


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


def pack(data, bounds=None):
    fields = split.split(data, *(bounds if bounds is not None else code_bounds(data)))
    payload = compress(fields)
    header = struct.pack("<4sII", b"HDL1", len(data), len(fields))
    return header + struct.pack("<I", zlib.crc32(payload, zlib.crc32(header))) + payload


def unpack(data, limit=256 << 20):
    magic, size, need, crc = struct.unpack_from("<4sIII", data)
    payload = data[16:]
    if magic != b"HDL1" or max(size, need) > limit:
        raise ValueError("invalid header or size limit exceeded")
    if zlib.crc32(payload, zlib.crc32(data[:12])) != crc:
        raise ValueError("checksum mismatch")
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[LZMA])
    fields = decoder.decompress(payload, max_length=need + 1)
    if len(fields) != need or not decoder.eof or decoder.unused_data:
        raise ValueError("invalid compressed length")
    return split.join(fields, size)


def compare(data, bounds=None):
    fields = split.split(data, *(bounds if bounds is not None else code_bounds(data)))
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
            bcj2, len(compress(fields)))


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
