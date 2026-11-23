/* Restore the field streams written by split.py and clear the scratch buffer. */

#include <stdint.h>
#ifdef _MSC_VER                /* MSVC, for the packer: its own words for the same */
#include <string.h>
#define __builtin_memcpy memcpy
#define __attribute__(x)
#endif

enum {                         /* the streams, in the order they are packed: the best */
    S_SIB, S_J8, S_D8, S_D8S, S_D8L, S_D32, S_D32S, S_D32L, S_RIP, S_I8, S_ISH, S_I32,
    S_IMR, S_I64, S_JCC, S_JMP, S_CALL, S_MOV, S_OP, NS
};

/* Each opcode's nibble, the even opcode's in the low half: 8 when a ModRM
   byte follows, plus what follows the operands: 1 an 8-bit immediate,
   2 a 16- or 32-bit one, 3 that or a 64-bit one (MOV r, imm), 4 an 8-bit
   jump, 5 a call's 32-bit distance, 6 a jump's, 7 TEST's immediate when
   its ModRM says so (F6, F7). one is the opcodes of one byte, two those
   after 0F. Made by split.py, from its own. Aligned as bytes:
   GCC would put them on 32, which a small stub cannot start on. */
#define BYTES __attribute__((aligned(1)))
static const uint8_t one[128] BYTES = {
    0x88, 0x88, 0x21, 0x00, 0x88, 0x88, 0x21, 0x00, 0x88, 0x88, 0x21, 0x00,
    0x88, 0x88, 0x21, 0x00, 0x88, 0x88, 0x21, 0x00, 0x88, 0x88, 0x21, 0x00,
    0x88, 0x88, 0x21, 0x00, 0x88, 0x88, 0x21, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x88, 0x00, 0x00, 0xA2, 0x91, 0x00, 0x00, 0x44, 0x44, 0x44, 0x44,
    0x44, 0x44, 0x44, 0x44, 0xA9, 0x90, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x21, 0x00, 0x00, 0x00, 0x11, 0x11, 0x11, 0x11, 0x33, 0x33, 0x33, 0x33,
    0x99, 0x00, 0x88, 0xA9, 0x00, 0x00, 0x10, 0x00, 0x88, 0x88, 0x00, 0x00,
    0x88, 0x88, 0x88, 0x88, 0x11, 0x11, 0x11, 0x11, 0x65, 0x40, 0x00, 0x00,
    0x00, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x00, 0x88
};
static const uint8_t two[128] BYTES = {
    0x88, 0x88, 0x08, 0x00, 0x00, 0x08, 0x88, 0x80, 0x88, 0x88, 0x88, 0x88,
    0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88,
    0x00, 0x00, 0x00, 0x08, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88,
    0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88,
    0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x99, 0x99, 0x88, 0x08,
    0x88, 0x88, 0x88, 0x88, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66,
    0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x00, 0x80, 0x89, 0x88,
    0x00, 0x80, 0x89, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x89, 0x88, 0x88,
    0x88, 0x89, 0x99, 0x89, 0x00, 0x00, 0x00, 0x00, 0x88, 0x88, 0x88, 0x88,
    0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88,
    0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88
};

static const uint8_t order[NS] BYTES = {
    S_SIB, S_J8, S_D8, S_D8L, S_D8S, S_D32, S_D32S, S_D32L, S_RIP, S_ISH, S_I8, S_I32, S_IMR, S_I64, S_JCC, S_JMP, S_CALL, S_MOV, S_OP
};

static uint8_t *dst, *st[NS + 1];     /* the streams, then the data */
static uint32_t o, lim;

#ifdef HEDDLE_CHECKED
static uint8_t *end[NS + 1];
static int failed;
#endif

/* Moves the next n bytes of stream s to the program, as far as lim, and
   gives the last; each byte read from a stream is left zero. */
__attribute__((noinline)) static unsigned take(unsigned s, unsigned n)
{
    unsigned b = 0;

#ifdef HEDDLE_CHECKED
    if ((uint32_t)(end[s] - st[s]) < n || o > UINT32_MAX - n) {
        failed = 1;
        return 0;
    }
#endif
    while (n--) {
        b = *st[s];
        *st[s]++ = 0;
        if (o < lim)
            dst[o] = (uint8_t)b;
        o++;
    }
    return b;
}

#ifdef HEDDLE_CHECKED
int heddle_join(uint8_t *to, uint32_t n, uint8_t *src, uint32_t size, int kind)
#else
void pn_unsplit(uint8_t *to, uint32_t n, uint8_t *src)
#endif
{
    uint8_t *p;
    uint32_t at, v, i, lo = 0, hi = 0, zero = 0;
    unsigned op, code, m, p66, w, esc, s, k;

#ifdef HEDDLE_CHECKED
    unsigned legacy = kind == 'L';
    if (size < 4 * (NS + 2 - legacy) || n > 0xffffff00u)
        return 1;
    failed = 0;
#else
    const unsigned legacy = 0;
#endif
    p = src + 4 * (NS + 2 - legacy);
    dst = to;
    o = 0;
    for (i = 0; i < NS + 2 - legacy; i++) {     /* where the code is, then the lengths */
        __builtin_memcpy(&v, src + 4 * i, 4);
        __builtin_memcpy(src + 4 * i, &zero, 4);
        if (i == 0)
            lo = v;
        else if (i == 1)
            hi = v;
        else {
            unsigned stream = legacy ? i - 2 : order[i - 2];
            st[stream] = p;
#ifdef HEDDLE_CHECKED
            if (v > size - (uint32_t)(p - src))
                return 1;
            if (kind == 'X' && stream == S_JCC) {
                if (v % 4)
                    return 1;
                uint32_t previous = 0;
                for (uint32_t j = 0; j < v; j += 4) {
                    uint32_t value;
                    __builtin_memcpy(&value, p + j, 4);
                    previous ^= value;
                    __builtin_memcpy(p + j, &previous, 4);
                }
            }
#endif
            p += v;
#ifdef HEDDLE_CHECKED
            end[stream] = p;
#endif
        }
    }
#ifdef HEDDLE_CHECKED
    if (legacy) {
        st[S_OP] = st[S_MOV];
        end[S_OP] = end[S_MOV];
        end[S_MOV] = st[S_MOV];
    }
    if (lo > hi || hi > n || size - (uint32_t)(p - src) != n - (hi - lo))
        return 1;
    end[NS] = src + size;
#endif
    st[NS] = p;
    lim = hi;
    take(NS, lo);                      /* the data before the code */
    while (o < hi) {
        at = p66 = w = m = 0;
        for (;;) {                     /* the prefixes, then the opcode */
            op = take(S_OP, 1);
            if (op != 0x66 && op != 0x67 && op != 0xF2 && op != 0xF3 && op != 0xF0
                && op != 0x2E && op != 0x3E && op != 0x26 && op != 0x64 && op != 0x65
                && op != 0x36)
                break;
            p66 |= op == 0x66;
        }
        if ((op & 0xF0) == 0x40) {     /* REX */
            w = op & 8;
            op = take(S_OP, 1);
        }
        esc = op == 0x0F;
        if (esc) {
            op = take(S_OP, 1);
            if (op == 0x38 || op == 0x3A) {
                code = op == 0x3A ? 9 : 8;
                take(S_OP, 1);
            } else
                code = (two[op >> 1] >> (op & 1) * 4) & 15;
        } else
            code = (one[op >> 1] >> (op & 1) * 4) & 15;
        if (code & 8) {                /* ModRM, SIB and displacement */
            m = take(S_OP, 1);
            if (m >> 6 != 3) {
                s = op == 0x8D ? 2 : 0; /* LEA's, and those off the stack pointer: apart */
                if ((m & 7) == 4) {
                    k = take(S_SIB, 1) & 7;
                    s = s ? s : k == 4;
                    if (k == 5 && m >> 6 == 0)
                        take(S_D32, 4);
                } else if ((m & 0xC7) == 5) {
                    at = o + 1;
                    take(S_RIP, 4);
                }
                if (m >> 6 == 1)
                    take(S_D8 + s, 1);
                else if (m >> 6 == 2)
                    take(S_D32 + s, 4);
            }
        }
        code &= 7;                     /* the immediate or distance */
        if (code == 7)
            code = m >> 3 & 6 ? 0 : op == 0xF6 ? 1 : 2;
        k = 4;
        s = !legacy && op == 0xc7 ? S_MOV : S_I32;
        if (code == 1 || code == 4)
            k = 1, s = code == 4 ? S_J8 : (op & 0xFE) == 0xC0 ? S_ISH : S_I8;
        else if (code == 2)
            k = p66 ? 2 : 4;
        else if (code == 3)
            k = w ? 8 : p66 ? 2 : 4, s = w ? S_I64 : S_IMR;
        else if (code == 5)
            at = o + 1, s = S_CALL;
        else if (code == 6 && esc)
            at = o + 1, s = S_JCC;
        else if (code == 6)
            s = S_JMP;
        if (code)
            take(s, k);
        /* A call's or a conditional jump's target, or a RIP-relative
           address, back to a distance from the instruction's end. */
#ifdef HEDDLE_CHECKED
        if (failed)
            return 1;
#endif
        if (at-- && o <= hi) {
            __builtin_memcpy(&v, dst + at, 4);
            v -= o;
            __builtin_memcpy(dst + at, &v, 4);
        }
    }
    o = hi;                            /* and after it */
    lim = n;
    take(NS, n - hi);
#ifdef HEDDLE_CHECKED
    for (i = 0; i <= NS; i++)
        if (st[i] != end[i])
            return 1;
    return failed;
#endif
}
