# heddle

![MIT license](https://shieldcn.dev/badge/License-MIT-527a79.svg?variant=secondary)

Heddle is a filter that helps LZMA compress x86-64 machine code. Before compression it sorts each instruction's fields into separate streams, so opcodes sit next to opcodes and displacements next to displacements. It was built for Pinion, a game that has to fit on one floppy disk. On Pinion's core, version 2 of the format comes out 121,299 bytes smaller than BCJ2, and version 1 came out 93,710 bytes smaller. The wire format is documented in [FORMAT.md](FORMAT.md).

| Input | Original | Plain LZMA | x86 BCJ + LZMA | BCJ2 + LZMA | Heddle 1 (HDL1) | Heddle 2 |
|---|---:|---:|---:|---:|---:|---:|
| Game core | 1,380,260 | 718,075 | 694,385 | 682,071 | 588,361 | 560,772 |
| true | 32,304 | 11,787 | 11,343 | 11,244 | 11,060 | 11,070 |
| ls | 153,400 | 62,685 | 60,315 | 58,679 | 56,443 | 55,602 |
| sed | 119,888 | 54,021 | 51,483 | 50,068 | 48,116 | 47,105 |
| xz | 103,312 | 41,058 | 38,382 | 37,889 | 35,267 | 34,713 |
| Linked PE | 15,872 | 7,593 | 7,508 | 7,441 | 7,125 | 7,127 |

On the four Unix programs, BCJ2 already gets most of the gain. On `true`, version 2 is only 174 bytes smaller than BCJ2 (version 1 was 184 bytes smaller), less than the extra size of Heddle's decoder. For small programs, a custom decoder can cost more than the filter saves.

| Decoder | unpack.c (LZMA and member) | Filter | Total |
|---|---:|---:|---:|
| Plain LZMA1 | 1,365 | 0 | 1,365 |
| XZ x86 BCJ | 1,365 | 448 | 1,813 |
| 7-Zip BCJ2 | 1,365 | 1,043 | 2,408 |
| Heddle 1, trusted raw path | 1,328 | 1,377 | 2,705 |
| Heddle 1, checked member | 1,548 | 1,550 | 3,098 |
| Heddle 2, trusted raw path | 1,365 | 1,418 | 2,783 |
| Heddle 2, checked member | 2,473 | 1,721 | 4,194 |

Every column uses the same inputs. The game core is the raw image of Pinion commit c00684d, with its code at file offsets [8192, 1250955). The other five are fixed programs from a private corpus. Sizes cover the whole input file. Heddle filters only the .text section (for the raw image, the linked code range), while BCJ and BCJ2 scan the whole file. Plain LZMA, x86 BCJ and both Heddle columns use Python's liblzma with raw LZMA1, an 8 MiB dictionary and nice length 96. That is the default profile, and the core uses it too. Heddle 1 is the released version 1 writer (commit 2a1ca7b). BCJ2 is 7-Zip 26.04 with three LZMA streams at the same settings plus a range-coded control stream, and its size is the packed size 7-Zip reports. Container headers are left out: a Heddle member adds a 16-byte header on top. The nine-byte headers of a version 2 member's blocks are part of the payload, so they are counted.

Decoder sizes are for x86-64, built with GCC 16.2 using `-Os -ffreestanding -fno-asynchronous-unwind-tables`, and count code plus read-only data. Heddle's rows compile each version's `unpack.c` and `unsplit.c` with and without `HEDDLE_CHECKED`. The checked `unpack.c` also handles the member header, checksum, blocks and dictionary. Version 2's checked reader is bigger mainly because of the blocks, the dictionary and the whole-stream rule (FORMAT.md). The XZ 5.8.2 figure covers the standalone decode entry point, the shared x86 conversion routine and its mask table. The 7-Zip 26.01 figure covers `Bcj2Dec_Init` and `Bcj2Dec_Decode`. Both were measured once from source and paired with Heddle's trusted LZMA decoder for comparison. A real BCJ2 integration would also need storage and dispatch for its multiple streams.

## Try a file

The packer is `pack.c`, written in C11, with the LZMA encoder vendored in `lzma/`. It has no dependencies and builds with a single command using GCC or Clang on Linux, or Apple's clang on macOS. An arm64 Mac can pack x86-64 programs too. Run these commands from the repository root:

```sh
cc -O2 -DHEDDLE_CHECKED -D_7ZIP_ST pack.c unpack.c unsplit.c lzma/LzmaEnc.c lzma/LzFind.c lzma/Bra86.c -o heddle
./heddle compare /usr/bin/ls
./heddle pack /usr/bin/ls ls.hdl
./heddle unpack ls.hdl ls.out
cmp /usr/bin/ls ls.out
./heddle pack image.bin image.hdl --code 0x2000 0x8000
```

On Windows, build it with MSVC from a Developer Command Prompt (Visual Studio 2022 Build Tools). MinGW hasn't been tested.

```bat
cl /O2 /DHEDDLE_CHECKED /D_7ZIP_ST pack.c unpack.c unsplit.c lzma/LzmaEnc.c lzma/LzFind.c lzma/Bra86.c /Fe:heddle.exe
```

Heddle finds the code in little-endian x86-64 ELF and PE files by their .text section. For files without section headers, and for raw images, pass the code's file offsets with `--code LO HI`, where `LO` is inclusive and `HI` is exclusive. `compare` also measures BCJ2 if `7z` or `7zz` is installed. Every `pack` decodes its output with the C reader before writing it. `unpack` restores the file's contents but not its permissions.

`pack.c` is the packer to use. `heddle.py`, with `split.py` and `opcodes.py`, is the Python reference implementation the format was developed in, and `tests/pack.py` checks the C packer against it. `python3 heddle.py` accepts the same commands as `./heddle`. Its API is:

- `heddle.pack(data, bounds=None, profile=heddle.LZMA)`. A profile can change how hard the encoder searches, but not the stream's lc/lp/pb settings or a window over 8 MiB, since the member doesn't record them.
- `heddle.unpack(member, limit=256 << 20)`
- `heddle.compare(data, bounds=None)`

The encoder in `lzma/` is Igor Pavlov's LZMA SDK 9.20 from [7-zip.org](https://www.7-zip.org/a/lzma920.tar.bz2), unmodified and in the public domain (`lzma/LICENSE`). It packs Pinion's core to 560,428 bytes, close to liblzma's 560,389 and better than the SDK's latest release, 26.01, at 561,482. On the five other programs it packs to 155,490 bytes, against 155,617 from the Python packer. To compress with liblzma instead, build with `-DHEDDLE_LIBLZMA -llzma` in place of `lzma/LzmaEnc.c` and `lzma/LzFind.c`. That build's output is byte-identical to the Python packer's with the same settings.

## Embed the decoder

Compile `unpack.c` and `unsplit.c` with `HEDDLE_CHECKED` defined, and include `heddle.h`. The decoder uses only integer arithmetic, fixed static tables and four-byte copies. It needs a little-endian machine and never allocates memory.

```sh
cc -std=c11 -Os -ffreestanding -DHEDDLE_CHECKED -c unpack.c unsplit.c
```

```c
#include "heddle.h"

int restore(const uint8_t *member, uint32_t member_bytes,
            uint8_t *image, uint32_t image_capacity,
            uint8_t *scratch, uint32_t scratch_capacity)
{
    return heddle_decode(member, member_bytes, image, image_capacity,
                         scratch, scratch_capacity);
}
```

To size the buffers, read the member's 16-byte header first. Its first four bytes are a tag naming the layout. Version 2 writes `HDS2` for a single LZMA stream, `HDB2` for independently compressed blocks, and `HDX2` for blocks whose consecutive long conditional branch targets are XORed. Version 2 readers also read version 1's `HDL1`. The little-endian u32s at offsets 4 and 8 give the output size and the scratch size. Check both against your application's memory budget before allocating, and pass each buffer's real capacity. On success the decoder clears the part of the scratch buffer it used. For a dictionary block, the decoder briefly uses the output buffer to hold the block's intermediate bytes before it rebuilds the fields. [FORMAT.md](FORMAT.md) documents the full layout, the size limits and the rule both readers use to accept an LZMA stream.

Peak memory is the member, the output and the scratch buffer, plus 16,406 bytes of static decoder state (measured on x86-64) and a small stack. In version 2, scratch is usually the image size plus 84 bytes, plus padding for at most one partial instruction. The three buffers must not overlap, and calls must not run concurrently. All stored sizes are 32-bit. The CRC-32 catches accidental damage, and the checked decoder also checks every stream boundary independently of the CRC. Builds without `HEDDLE_CHECKED` instead expose the low-level routines `heddle_unpack` and `heddle_unsplit`, for stubs that decode trusted, already verified streams.

## Why the transform works

Opcodes tend to resemble other opcodes, and stack offsets tend to repeat. In ordinary machine code these fields are interleaved, which hides the repetition from LZMA. Heddle walks the instructions and sorts their fields into 19 streams, such as opcodes, displacements and immediates. Calls, long conditional branches and RIP-relative references are stored as offsets into the image, so every reference to the same target has the same bytes. LZMA then finds those repeats close together.

The decoder's walker decides each step using only bytes it has already restored, so it makes the same choices as the packer's. An unfamiliar instruction, such as one with a vector prefix, can knock the walker out of step with the real instruction boundaries. That costs compression but not correctness: both walkers still agree, even on a partial final instruction. And since every pack decodes its output before writing it, a member the walkers disagree on is never written.

Heddle builds on earlier work. BCJ converts branch targets, BCJ2 moves branch operands into separate streams, and kkrunchy showed that grouping x86 instruction fields pays off for small executables. Heddle applies that approach to x86-64, with a C packer and a freestanding C decoder. See [XZ BCJ](https://tukaani.org/xz/liblzma-api/bcj_8h.html), [7-Zip BCJ2](https://github.com/ip7z/7zip/blob/main/C/Bcj2.h) and [kkrunchy's filter](https://fgiesen.wordpress.com/2011/01/24/x86-code-compression-in-kkrunchy/).

## Run the tests

`CC=clang python3 test.py` builds the decoder with AddressSanitizer and UndefinedBehaviorSanitizer and runs it on:

- round trips of local binaries, partial instructions and random code ranges, checking that the scratch buffer is cleared;
- a version 1 member from the released version 1 writer;
- damaged members, invalid sizes and malformed inner streams, with their checksums recomputed. Every damaged member must get the same verdict from the Python and C readers;
- exact LZMA symbol sequences from `tests/lzma1.py`, covering both sides of the whole-stream rule.

To test other files, pass them as arguments, writing raw images as `FILE:LO:HI`. On macOS, where programs are Mach-O, pass an x86-64 slice (`lipo -thin x86_64`) with the bounds of its `__text` section. With `CC=cl`, from a Developer Command Prompt, the tests build with MSVC and its AddressSanitizer. MSVC has no UndefinedBehaviorSanitizer.

`CC=clang python3 tests/pack.py` builds the C packer with both sanitizers, plus a liblzma build when liblzma is installed, and checks it against the Python packer. For every input:

- its fields match the Python split byte for byte;
- its opcode dictionary matches `opcodes.encode`'s;
- each packer's members decode with the other's reader;
- the liblzma build's members are identical to the Python packer's;
- both refuse malformed headers with the same messages.

It takes the same arguments as `test.py`, also supports `CC=cl`, and tries `compare` on the first input. It runs in about 20 seconds.

`python3 tests/mutate.py` first requires `test.py` to pass. It then removes each check from the two readers, one at a time, and requires `test.py` to fail, which shows that every check has its own test. It takes the same arguments as `test.py` and runs in about a minute and a half.
