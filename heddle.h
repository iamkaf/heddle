#ifndef HEDDLE_H
#define HEDDLE_H
#include <stdint.h>

/* Compile unpack.c and unsplit.c with -DHEDDLE_CHECKED. Little-endian host.
   A member starts with a four-byte tag (HDS2, HDB2, HDX2, or version 1's
   HDL1; FORMAT.md), then little-endian u32 output and scratch sizes and
   CRC32. Set your own resource limits before allocating either buffer.
   Limits, which the writer keeps and this reader checks: a member is at
   most 0xffffffff bytes, its image at most 0xffffff00, and its scratch at
   most 0xffffffff, or PTRDIFF_MAX on a host with 32-bit pointers.
   Pass three disjoint buffers with their actual lengths. Contents of dst
   and scratch are unspecified on error. Success clears the used scratch.
   Peak caller storage = input + output + scratch, plus fixed static state
   and a small call stack. Calls must be serialized. Zero means success. */
int heddle_decode(const uint8_t *src, uint32_t bytes, uint8_t *dst,
                  uint32_t capacity, uint8_t *scratch, uint32_t available);
#endif
