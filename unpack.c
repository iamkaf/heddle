/* unpack.c: the one decompressor, raw LZMA (LZMA1) with lc=3, lp=0, pb=2
   fixed, as Python's lzma module writes it with FORMAT_RAW. Standalone:
   no includes but <stdint.h>, no calls out, integer math only, so the
   self-unpacking executables can reuse it as is.

   pn_unpack(src, n, dst, size) writes exactly size bytes to dst and returns
   0, or non-zero if the stream is damaged. Whatever the bytes, it reads no
   further than src[n - 1] and writes no further than dst[size - 1]: past
   the end the input reads as zeros, every match distance is checked against
   what is already written, and every copy stops at size. The whole output
   is the dictionary, so there is no window to allocate. Its 16 KB of
   probabilities are static: a stack that big would need a stack probe. */

#include <stdint.h>

#define LC 3                   /* literal context: the previous byte's top bits */
#define PB 2                   /* position bits for the match and length contexts */

enum {                         /* offsets into p[], LZMA's probability tables */
    IS_MATCH = 0,              /* [state][pos] */
    IS_REP = 192,              /* [state], and the three below */
    IS_REP_G0 = 204,
    IS_REP_G1 = 216,
    IS_REP_G2 = 228,
    IS_REP0_LONG = 240,        /* [state][pos] */
    POS_SLOT = 432,            /* [length state][64] */
    POS_SPEC = 688,            /* the 115 for slots 4 to 13 */
    ALIGN = 803,               /* 16 */
    LEN = 819,                 /* 514 per length coder */
    REP_LEN = 1333,
    LIT = 1847,                /* [prev >> (8 - LC)][0x300] */
    NPROBS = LIT + (0x300 << LC)
};

static uint16_t p[NPROBS];
static const uint8_t *in;
static uint32_t inlen, inpos, range, code;

static uint32_t next(void)
{
    uint32_t b = inpos < inlen ? in[inpos] : 0;
    inpos++;
    return b;
}

static void norm(void)
{
    if (range < 1u << 24) {
        range <<= 8;
        code = code << 8 | next();
    }
}

static unsigned bit(uint16_t *q)
{
    uint32_t bound = (range >> 11) * *q;
    unsigned b = code >= bound;

    if (b) {
        range -= bound;
        code -= bound;
        *q -= *q >> 5;
    } else {
        range = bound;
        *q += (2048 - *q) >> 5;
    }
    norm();
    return b;
}

/* n bits, high first, through a tree of probabilities from q[1]. */
static unsigned tree(uint16_t *q, int n)
{
    unsigned m = 1;
    int i;

    for (i = 0; i < n; i++)
        m = m * 2 + bit(q + m);
    return m - (1u << n);
}

/* The same, low bit first. */
static unsigned rtree(uint16_t *q, int n)
{
    unsigned m = 1, s = 0, b;
    int i;

    for (i = 0; i < n; i++) {
        b = bit(q + m);
        m = m * 2 + b;
        s |= b << i;
    }
    return s;
}

/* A match length less 2: 0 to 271. */
static unsigned len(uint16_t *q, unsigned ps)
{
    if (!bit(q))
        return tree(q + 2 + ps * 8, 3);
    if (!bit(q + 1))
        return 8 + tree(q + 130 + ps * 8, 3);
    return 16 + tree(q + 258, 8);
}

int pn_unpack(const uint8_t *src, uint32_t n, uint8_t *dst, uint32_t size)
{
    uint32_t out = 0, rep[4] = { 0, 0, 0, 0 }, d, i;
    unsigned state = 0, ps, l, g;

    in = src;
    inlen = n;
    inpos = 1;                 /* the first byte is always 0 */
    range = 0xffffffff;
    code = 0;
    for (i = 0; i < 4; i++)
        code = code << 8 | next();
    for (i = 0; i < NPROBS; i++)
        p[i] = 1024;

    while (out < size) {
        ps = out & ((1 << PB) - 1);
        if (!bit(p + IS_MATCH + state * 16 + ps)) {
            /* A literal. After a match it is coded against the byte at
               rep[0], until the first bit that differs: offs is 0x100 while
               they agree and 0 after. */
            unsigned sym = 1, offs = state >= 7 ? 0x100 : 0, match, b, was;
            uint16_t *q = p + LIT + 0x300 * ((out ? dst[out - 1] : 0) >> (8 - LC));
            match = offs ? dst[out - rep[0] - 1] : 0;
            while (sym < 0x100) {
                match <<= 1;
                was = offs;
                offs &= match;
                b = bit(q + offs + was + sym);
                sym = sym * 2 + b;
                if (!b)
                    offs ^= was;
            }
            dst[out++] = (uint8_t)sym;
            state = state < 4 ? 0 : state < 10 ? state - 3 : state - 6;
            continue;
        }
        if (bit(p + IS_REP + state)) {
            /* A repeated distance, rep[g]; every rep is below out once
               out > 0. */
            if (!out)
                return 1;
            g = 0;
            if (bit(p + IS_REP_G0 + state))
                g = bit(p + IS_REP_G1 + state) ? 2 + bit(p + IS_REP_G2 + state) : 1;
            else if (!bit(p + IS_REP0_LONG + state * 16 + ps)) {
                state = state < 7 ? 9 : 11;
                dst[out] = dst[out - rep[0] - 1];
                out++;
                continue;
            }
            d = rep[g];
            l = len(p + REP_LEN, ps);
            state = state < 7 ? 8 : 11;
        } else {
            /* A new distance: a 6-bit slot, then its low bits. */
            g = 3;
            l = len(p + LEN, ps);
            state = state < 7 ? 7 : 10;
            d = tree(p + POS_SLOT + (l < 3 ? l : 3) * 64, 6);
            if (d >= 4) {
                unsigned nd = (d >> 1) - 1;
                uint32_t base = (2 | (d & 1)) << nd;
                if (d < 14) {
                    base += rtree(p + POS_SPEC + base - d, (int)nd);
                } else {
                    for (i = nd - 4; i; i--) {     /* direct bits, at even odds */
                        range >>= 1;
                        code -= range;
                        d = 0 - (code >> 31);
                        code += range & d;
                        base += (d + 1) << (i + 3);
                        norm();
                    }
                    base += rtree(p + ALIGN, 4);
                }
                d = base;
            }
            if (d >= out)      /* before the start; the end marker is ~0 */
                return 1;
        }
        for (; g; g--)         /* d moves to the front of the reps */
            rep[g] = rep[g - 1];
        rep[0] = d;
        for (l += 2; l-- && out < size; out++)
            dst[out] = dst[out - d - 1];
    }
    return inpos > n;
}

#ifdef HEDDLE_CHECKED
int heddle_join(uint8_t *, uint32_t, uint8_t *, uint32_t);

int heddle_decode(const uint8_t *src, uint32_t bytes, uint8_t *dst,
                  uint32_t capacity, uint8_t *scratch, uint32_t available)
{
    uint32_t size, need, stored, crc = ~0u;
    if (bytes < 21 || src[0] != 'H' || src[1] != 'D' || src[2] != 'L' || src[3] != '1')
        return 1;
    __builtin_memcpy(&size, src + 4, 4);
    __builtin_memcpy(&need, src + 8, 4);
    __builtin_memcpy(&stored, src + 12, 4);
    if (size > capacity || size > 0xffffff00u || need > available || need < 80)
        return 1;
    for (uint32_t i = 0; i < bytes; i++) {
        if (i >= 12 && i < 16)
            continue;
        crc ^= src[i];
        for (unsigned j = 0; j < 8; j++)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1)));
    }
    if (~crc != stored || pn_unpack(src + 16, bytes - 16, scratch, need))
        return 1;
    return heddle_join(dst, size, scratch, need);
}
#endif
