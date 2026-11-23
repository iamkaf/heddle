# heddle

![MIT license](https://shieldcn.dev/badge/License-MIT-527a79.svg?variant=secondary)

Heddle groups fields of x86-64 instructions before LZMA compression. It came out of Pinion, a game being built to fit on one floppy. On Pinion's core, version 2 of the format saves 121,299 bytes beyond BCJ2; version 1 saved 93,710. The wire format is in [FORMAT.md](FORMAT.md).

| Input | Original | Plain LZMA | x86 BCJ + LZMA | BCJ2 + LZMA | Heddle 1 (HDL1) | Heddle 2 |
|---|---:|---:|---:|---:|---:|---:|
| Game core | 1,380,260 | 718,075 | 694,385 | 682,071 | 588,361 | 560,772 |
| true | 32,304 | 11,787 | 11,343 | 11,244 | 11,060 | 11,070 |
| ls | 153,400 | 62,685 | 60,315 | 58,679 | 56,443 | 55,602 |
| sed | 119,888 | 54,021 | 51,483 | 50,068 | 48,116 | 47,105 |
| xz | 103,312 | 41,058 | 38,382 | 37,889 | 35,267 | 34,713 |
| Linked PE | 15,872 | 7,593 | 7,508 | 7,441 | 7,125 | 7,127 |

On these four Unix programs BCJ2 already gets most of the saving. On true, version 2 buys just 174 bytes beyond BCJ2, 10 fewer than version 1. A private decoder can cost more than the filter saves.

| Decoder | unpack.c (LZMA and member) | Filter | Total |
|---|---:|---:|---:|
| Plain LZMA1 | 1,365 | 0 | 1,365 |
| XZ x86 BCJ | 1,365 | 448 | 1,813 |
| 7-Zip BCJ2 | 1,365 | 1,043 | 2,408 |
| Heddle 1, trusted raw path | 1,328 | 1,377 | 2,705 |
| Heddle 1, checked member | 1,548 | 1,550 | 3,098 |
| Heddle 2, trusted raw path | 1,365 | 1,418 | 2,783 |
| Heddle 2, checked member | 2,473 | 1,721 | 4,194 |

All rows use the same inputs: the game core is the raw image of Pinion commit c00684d (code [8192, 1250955)), the others five fixed programs from a private corpus. Payload bytes cover the entire input file. Heddle splits only its .text section (the linked code interval for the raw image). BCJ and BCJ2 scan the whole input. Plain LZMA, x86 BCJ and both Heddle columns use Python's liblzma, raw LZMA1, an 8 MiB dictionary and nice length 96: the public default profile, also for the core. Heddle 1 is the released writer (commit 2a1ca7b). BCJ2 uses 7-Zip 26.04, three LZMA streams with those settings, and a range-coded control stream. Its count is the archive's reported packed size. Container headers are excluded throughout: a Heddle member adds its 16-byte header, while the nine-byte headers of a version 2 member's blocks are payload and counted.

Decoder bytes are x86-64 GCC 16.2 with `-Os -ffreestanding -fno-asynchronous-unwind-tables`, code and read-only constants. Heddle's rows compile each version's unpack.c and unsplit.c, without and with `HEDDLE_CHECKED`; the checked unpack.c also holds the member header, checksum, blocks and dictionary. Version 2's checked reader is larger mostly for its blocks, its dictionary and the whole-stream rule (FORMAT.md). The XZ 5.8.2 count covers the standalone decode entry, shared x86 conversion routine and mask table. The 7-Zip 26.01 count covers Bcj2Dec_Init and Bcj2Dec_Decode. Those two were measured once, from their sources, and are paired with our trusted LZMA decoder for scale. An actual BCJ2 integration also needs multi-stream storage and dispatch.

## Try a file

Python 3 with the standard lzma module runs the packer. Install nothing. Run these commands from the checkout.

```sh
./heddle compare /usr/bin/ls
./heddle pack /usr/bin/ls ls.hdl
./heddle unpack ls.hdl ls.out
cmp /usr/bin/ls ls.out
./heddle pack image.bin image.hdl --code 0x2000 0x8000
```

The CLI detects little-endian x86-64 ELF or PE files with a .text section. Specify file offsets for stripped section tables and raw blobs. The lower bound is inclusive and the upper bound exclusive. Compare uses the installed 7z or 7zz if available. The Python API is `heddle.pack(data, bounds=None, profile=heddle.LZMA)`, where a profile may change only how hard the encoder looks (FORMAT.md), `heddle.unpack(member, limit=256 << 20)` and `heddle.compare(data, bounds=None)`. Unpacking restores the file contents. File permissions belong to the caller.

## Embed the decoder

Compile both C files in checked mode and include heddle.h. The decoder uses integer operations, fixed static tables and four-byte compiler copies. It runs on little-endian machines and makes no allocations.

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

Read the first 16 bytes before sizing buffers. The four-byte tag identifies the layout: version 2 writes HDS2 for solid LZMA, HDB2 for independent blocks and HDX2 for blocks with consecutive long conditional targets XORed, and its readers still read version 1's HDL1. The little-endian u32s at offsets 4 and 8 give output and scratch lengths. Apply the memory budget of your application before allocating. Pass the actual available capacities. Success clears the used scratch. [FORMAT.md](FORMAT.md) has the whole layout, the limits and the rule for a whole LZMA stream, which both readers apply. A dictionary block's decoder borrows the image buffer for its intermediate before reconstructing the fields.

Peak storage is member plus output plus scratch, 16,406 bytes of fixed decoder state on the measured host, and a small call stack. Version 2 scratch usually takes the image size plus 84 bytes and up to one partial instruction's padding. The buffers must be disjoint and calls serialized. The stored sizes are 32-bit. CRC32 detects accidental damage. The checked API also checks stream boundaries independently of CRC. Builds without HEDDLE_CHECKED expose low-level routines for stubs consuming trusted, preverified streams.

## Why the transform works

An opcode tends to look like another opcode, and stack offsets tend to repeat. Heddle walks the instruction bytes and collects 19 streams for fields such as opcodes, displacements and immediates. Calls, long conditional branches and RIP-relative references to the same target get the same image offset. LZMA sees those repetitions together. The inverse walker makes each choice from bytes it has already restored. An unfamiliar opcode, such as a vector prefix, can throw off instruction alignment and hurt compression. Both walkers still agree, even at a partial final instruction. Every pack checks the split against the Python inverse.

BCJ converts branch targets, BCJ2 separates branch operands, and kkrunchy already demonstrated grouping x86 instruction fields for small executables. Heddle applies that approach to x86-64 with a Python packer and a freestanding C inverse. [XZ BCJ](https://tukaani.org/xz/liblzma-api/bcj_8h.html) · [7-Zip BCJ2](https://github.com/ip7z/7zip/blob/main/C/Bcj2.h) · [kkrunchy's filter](https://fgiesen.wordpress.com/2011/01/24/x86-code-compression-in-kkrunchy/)

## Run the native tests

`CC=clang python3 test.py` compiles the public decoder with AddressSanitizer and UndefinedBehaviorSanitizer. It round-trips local binaries, partial instructions and random ranges, verifies scratch clearing, reads a version 1 member from the released writer, and exercises damaged members, invalid sizes and malformed inner streams with recomputed checksums. Every damaged member must get the same verdict from the Python and C readers. `tests/lzma1.py` writes exact LZMA symbol sequences for both sides of the whole-stream rule. Supply other files as arguments, with raw images written as FILE:LO:HI.

`python3 tests/mutate.py` removes each check of the two readers in turn and requires test.py to fail, so every check has a test of its own. It takes about a minute and a half.
