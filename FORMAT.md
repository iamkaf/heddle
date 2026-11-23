# Heddle member format, version 2

This is the wire format that `heddle.py` writes and that `heddle.py` and the checked C decoder (`unpack.c`, `unsplit.c`) read. Version 2 is frozen: the tags below always mean the layouts below, and any incompatible change takes a new tag. All integers are little-endian.

## Header

| Offset | Size | Field |
|---:|---:|---|
| 0 | 4 | Tag: `HDS2`, `HDB2`, `HDX2`, or version 1's `HDL1` |
| 4 | 4 | Image size in bytes, at most 0xffffff00 |
| 8 | 4 | Fields size (the reader's scratch), at least 80 |
| 12 | 4 | CRC-32 (zlib's) of bytes 0–11, then of bytes 16 to the end |

The payload starts at offset 16 and runs to the end of the member. A member is at most 0xffffffff bytes. On a host with 32-bit pointers, the C reader also refuses a fields size over `PTRDIFF_MAX`.

| Tag | Payload |
|---|---|
| `HDS2` | One LZMA stream, lc/lp/pb 3/0/2, of the fields |
| `HDB2` | Blocks, which together make the fields |
| `HDX2` | Blocks, then the JCC stream's XOR undone |
| `HDL1` | Version 1: one LZMA stream, lc/lp/pb 3/0/2, of the version 1 fields |

## Fields

The fields are 21 u32s, then the 19 streams in the same order, then the data:

1. `lo` and `hi`, the code's file offsets: `lo` inclusive, `hi` exclusive, `lo ≤ hi ≤ image size`.
2. The 19 stream lengths, in this order: SIB, J8, D8, D8L, D8S, D32, D32S, D32L, RIP, ISH, I8, I32, IMR, I64, JCC, JMP, CALL, MOV, OP.
3. The streams, concatenated in that order.
4. The data: the image's bytes before `lo`, then those from `hi` to the end.

The streams hold the code's instructions, field by field, as the walker in `split.py` (`walk`) and `unsplit.c` reads them: OP takes prefixes, REX, opcode bytes and ModRM; SIB the SIB byte; D8 and D32 displacements, with the S streams for those off the stack pointer and the L streams for LEA's; RIP the RIP-relative displacements; ISH the shift counts; I8, I32, IMR and I64 the other immediates; MOV the 32-bit immediate of opcode C7; J8, JMP, JCC and CALL the branch distances. The opcode tables are `ONE` and `TWO` in `split.py`. Past the code's end the walker reads zeros, so the last instruction may take bytes of padding.

For a complete instruction (one that ends at or before `hi`), a CALL, JCC or RIP field holds its target as a file offset: the stored distance plus `lo`, plus the instruction's end within the code, modulo 2³². Every stream is used up exactly, and the image is exactly `image size` bytes.

## Blocks

Each block is a property byte, a u32 compressed length and a u32 expanded length, then its compressed bytes. The blocks' expanded bytes, in order, make the fields; their expanded lengths sum to the fields size. A block owns exactly its compressed bytes.

- **Properties 0–224:** an LZMA stream with `lc = p % 9`, `lp = p / 9 % 5` and `pb = p / 45`. A reader refuses `lc + lp > 3`.
- **Property 253, a dictionary block:** a u32 intermediate size of at most the image size, then an LZMA stream, lc/lp/pb 3/0/0, of that many bytes, which the dictionary rules below turn into the block's expanded bytes.
- Every other property byte is refused.

In `HDX2`, the JCC stream's length is a multiple of 4, and each of its u32s was stored XORed with the u32 before it (the first with 0). A reader XORs each stored u32 with the value it restored before.

## Dictionary

The intermediate bytes are a u16 row count from 1 to 65,025, then a table of eight columns of that many bytes, then tags.

- **The table:** row `i` is a length (column 0) and up to seven bytes (columns 1–7). Each column is delta coded: its first byte as it is, each later byte as its difference from the byte before, modulo 256.
- **Short tags:** `short = 255 − (count − 1) / 255`, rounded down. Tags 1 to `short` name rows 0 to `short − 1`.
- **Long tags:** a tag above `short` takes the next byte `x` and names row `short + x × (255 − short) + (tag − 1 − short)`.
- **Literals:** tag 0 takes the next byte `n`, from 1 to 255, and copies the `n` bytes after it.

A row is checked when a tag names it: its index must be below the count and its length from 1 to 7. Rows no tag names are not checked. The tags must make exactly the block's expanded length, and end with the intermediate bytes.

## A whole LZMA stream

Every LZMA stream in a member is raw LZMA1 with an end marker, decoded with an 8 MiB window. A stream is whole when:

1. its first byte is 0;
2. it makes exactly the stated number of bytes, and no match runs past them;
3. every match distance is inside both what is already written and the 8 MiB window;
4. an end marker (distance 2³² − 1, with any length) follows the last byte;
5. the range coder's code is then 0;
6. and that is the last byte the stream owns.

This is the rule liblzma applies to such a stream; the C decoder applies the same one, so both readers accept and refuse the same members.

## Version 1

`HDL1` members have 20 u32s: `lo`, `hi` and 18 stream lengths, with no MOV stream (opcode C7's immediate is in I32), in the order SIB, J8, D8, D8S, D8L, D32, D32S, D32L, RIP, I8, ISH, I32, IMR, I64, JCC, JMP, CALL, OP. Version 2 readers read them. Version 1 readers refuse every version 2 tag. `tests/v1.hdl` is a member written by the version 1 writer (commit 2a1ca7b) from `tests/v1.bin`, and the tests read it with both version 2 readers.
