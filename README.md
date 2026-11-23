# heddle

![MIT license](https://shieldcn.dev/badge/License-MIT-527a79.svg?variant=secondary)

Heddle groups fields of x86-64 instructions before LZMA compression. It came out of Pinion, a game being built to fit on one floppy. The original HDL1 measurements below used an earlier core image. The filter saved 94,073 bytes beyond BCJ2.

| Input | Original | Plain LZMA | x86 BCJ + LZMA | BCJ2 + LZMA | Heddle + LZMA |
|---|---:|---:|---:|---:|---:|
| Game core | 1,378,628 | 717,194 | 693,513 | 681,095 | 587,022 |
| true | 32,304 | 11,787 | 11,343 | 11,244 | 11,060 |
| ls | 153,400 | 62,685 | 60,315 | 58,679 | 56,443 |
| sed | 119,888 | 54,021 | 51,483 | 50,068 | 48,116 |
| xz | 103,312 | 41,058 | 38,382 | 37,889 | 35,267 |
| Linked PE | 15,872 | 7,593 | 7,508 | 7,441 | 7,125 |

On these four Unix programs BCJ2 already gets most of the saving. On true, Heddle buys just 184 bytes beyond BCJ2. A private decoder can cost more than the filter saves.

| Decoder | Filter code and constants | Including common LZMA core |
|---|---:|---:|
| Plain LZMA1 | 0 | 1,376 |
| XZ x86 BCJ | 448 | 1,824 |
| 7-Zip BCJ2 | 1,043 | 2,419 |
| Heddle trusted raw path | 1,425 | 2,801 |
| Heddle public checked member | 1,598 | 3,194 |

Payload bytes cover the entire input file. Heddle splits only its .text section (the linked code interval for the raw image). BCJ and BCJ2 scan the whole input. The first, second and fourth compression columns use Python's liblzma, raw LZMA1, an 8 MiB dictionary and nice length 96. BCJ2 uses 7-Zip 26.04, three LZMA streams with those settings, and a range-coded control stream. Its count is the archive's reported packed size. Container headers are excluded throughout. A Heddle member adds 16 bytes.

Decoder bytes are x86-64 GCC 16.2 with `-Os -fno-asynchronous-unwind-tables`, code and read-only constants. The XZ 5.8.2 count covers the standalone decode entry, shared x86 conversion routine and mask table. The 7-Zip 26.01 count covers Bcj2Dec_Init and Bcj2Dec_Decode. The totals for those two pair their measured filter with our raw LZMA decoder for scale. An actual BCJ2 integration also needs multi-stream storage and dispatch. The checked Heddle total includes bounds and CRC checks.

## Try a file

Python 3 with the standard lzma module runs the packer. Install nothing. Run these commands from the checkout.

```sh
./heddle compare /usr/bin/ls
./heddle pack /usr/bin/ls ls.hdl
./heddle unpack ls.hdl ls.out
cmp /usr/bin/ls ls.out
./heddle pack image.bin image.hdl --code 0x2000 0x8000
```

The CLI detects little-endian x86-64 ELF or PE files with a .text section. Specify file offsets for stripped section tables and raw blobs. The lower bound is inclusive and the upper bound exclusive. Compare uses the installed 7z or 7zz if available. The Python API is `heddle.pack(data, bounds=None)`, `heddle.unpack(member, limit=256 << 20)` and `heddle.compare(data, bounds=None)`. Unpacking restores the file contents. File permissions belong to the caller.

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

Read the first 16 bytes before sizing buffers. The four-byte tag identifies the layout. HDL1 is the original 18-stream member. Version 2 groups 19 fields and uses HDS2 for solid LZMA, HDB2 for independent blocks and HDX2 for blocks with consecutive long conditional targets XORed. The little-endian u32s at offsets 4 and 8 give output and scratch lengths. Apply the memory budget of your application before allocating. Offset 12 holds CRC32 of the first 12 bytes followed by the compressed payload. Pass the actual available capacities. Success clears the used scratch.

Each independent block starts with a property byte and two little-endian u32 lengths, compressed and expanded. Normal blocks use the LZMA property byte. Property 253 selects a dictionary of instruction forms. Its payload starts with a u32 intermediate size, then LZMA1 with lc/lp/pb 3/0/0. The decoder borrows the image buffer for this intermediate before reconstructing the fields.

Peak storage is member plus output plus scratch, 16,406 bytes of fixed decoder state on the measured host, and a small call stack. Version 2 scratch usually takes the image size plus 84 bytes and up to one partial instruction's padding. The buffers must be disjoint and calls serialized. The stored sizes are 32-bit. CRC32 detects accidental damage. The checked API also checks stream boundaries independently of CRC. Builds without HEDDLE_CHECKED expose low-level routines for stubs consuming trusted, preverified streams.

## Why the transform works

An opcode tends to look like another opcode, and stack offsets tend to repeat. Heddle walks the instruction bytes and collects 19 streams for fields such as opcodes, displacements and immediates. Calls, long conditional branches and RIP-relative references to the same target get the same image offset. LZMA sees those repetitions together. The inverse walker makes each choice from bytes it has already restored. An unfamiliar opcode, such as a vector prefix, can throw off instruction alignment and hurt compression. Both walkers still agree, even at a partial final instruction. Every pack checks the split against the Python inverse.

BCJ converts branch targets, BCJ2 separates branch operands, and kkrunchy already demonstrated grouping x86 instruction fields for small executables. Heddle applies that approach to x86-64 with a Python packer and a freestanding C inverse. [XZ BCJ](https://tukaani.org/xz/liblzma-api/bcj_8h.html) · [7-Zip BCJ2](https://github.com/ip7z/7zip/blob/main/C/Bcj2.h) · [kkrunchy's filter](https://fgiesen.wordpress.com/2011/01/24/x86-code-compression-in-kkrunchy/)

## Run the native tests

`CC=clang python3 test.py` compiles the public decoder with AddressSanitizer and UndefinedBehaviorSanitizer. It round-trips local binaries, partial instructions and random ranges, verifies scratch clearing, and exercises damaged members, invalid sizes and malformed inner streams with recomputed checksums. Supply other files as arguments, with raw images written as FILE:LO:HI.
