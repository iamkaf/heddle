/* heddle: pack, unpack and compare x86-64 programs (README.md). The
   packer splits the code into the field streams of split.py, writes a
   version 2 member (FORMAT.md) and reads it back with the C reader before
   it is written out. heddle.py is the reference it is held to (tests/pack.py).

   cc -O2 -DHEDDLE_CHECKED -D_7ZIP_ST pack.c unpack.c unsplit.c lzma/LzmaEnc.c lzma/LzFind.c lzma/Bra86.c -o heddle

   With -DHEDDLE_LIBLZMA and -llzma in place of lzma/LzmaEnc.c and
   lzma/LzFind.c, liblzma compresses, and the members are the Python
   packer's byte for byte, given the same setting (PROFILE in tests/pack.py). */
#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE               /* macOS: mkdtemp, which POSIX has but Apple hides */
#define _CRT_SECURE_NO_WARNINGS        /* MSVC: fopen, strerror and getenv are fine */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "heddle.h"
#ifdef HEDDLE_LIBLZMA
#include <lzma.h>
#else
#include "lzma/LzmaEnc.h"
#endif
#include "lzma/Bra.h"
#ifdef _WIN32
#include <direct.h>
#define popen _popen
#define pclose _pclose
#else
#include <unistd.h>
#endif

#define LIMIT 0xFFFFFFFFu               /* every length in a member is a u32 */

static void die(const char *what)
{
    fprintf(stderr, "heddle: %s\n", what);
    exit(1);
}

typedef struct {
    uint8_t *b;
    size_t n, cap;
} buf;

static void grow(buf *x, size_t n)
{
    if (n > SIZE_MAX - x->n)
        die("out of memory");
    if (x->n + n <= x->cap)
        return;
    size_t cap = x->cap ? x->cap : 256;
    while (cap < x->n + n)
        cap = cap > SIZE_MAX / 2 ? x->n + n : cap * 2;
    if (!(x->b = realloc(x->b, cap)))
        die("out of memory");
    x->cap = cap;
}

static void put(buf *x, const void *p, size_t n)
{
    grow(x, n);
    if (n)
        memcpy(x->b + x->n, p, n);
    x->n += n;
}

static void put1(buf *x, unsigned v)
{
    uint8_t b = (uint8_t)v;
    put(x, &b, 1);
}

static void store4(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v, p[1] = (uint8_t)(v >> 8), p[2] = (uint8_t)(v >> 16), p[3] = (uint8_t)(v >> 24);
}

static void put4(buf *x, uint32_t v)
{
    grow(x, 4);
    store4(x->b + x->n, v);
    x->n += 4;
}

static uint32_t load4(const uint8_t *p)
{
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* ---- The split, as split.py walks it ---- */

enum { SIB, J8, D8, D8S, D8L, D32, D32S, D32L, RIP, I8, ISH, I32, IMR, I64, JCC, JMP, CALL, MOV, OP, NS };
static const uint8_t ORDER[NS] = { SIB, J8, D8, D8L, D8S, D32, D32S, D32L, RIP, ISH, I8, I32, IMR, I64,
                                   JCC, JMP, CALL, MOV, OP };
enum { JCC_AT = 14 };                   /* JCC's place in ORDER */

/* What follows an opcode: 8 a ModRM byte, plus 1 imm8, 2 imm16/32,
   3 imm16/32/64, 4 rel8, 5 a call's rel32, 6 a jump's rel32, 7 TEST's
   immediate (F6, F7). ONE for opcodes of one byte, TWO for those after 0F. */
static uint8_t ONE[256], TWO[256];

static void tables(void)
{
    static const uint8_t modrm[] = { 0x62, 0x63, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x8B, 0x8C,
        0x8D, 0x8E, 0x8F, 0xC4, 0xC5, 0xD0, 0xD1, 0xD2, 0xD3, 0xD8, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD,
        0xDE, 0xDF, 0xFE, 0xFF };
    static const uint8_t other[][2] = { { 0x69, 10 }, { 0x6B, 9 }, { 0x80, 9 }, { 0x81, 10 },
        { 0x83, 9 }, { 0xC0, 9 }, { 0xC1, 9 }, { 0xC6, 9 }, { 0xC7, 10 }, { 0xF6, 15 }, { 0xF7, 15 },
        { 0x68, 2 }, { 0x6A, 1 }, { 0xA8, 1 }, { 0xA9, 2 }, { 0xCD, 1 }, { 0xE8, 5 }, { 0xE9, 6 },
        { 0xEB, 4 } };
    static const uint8_t bare[] = { 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0E, 0x30, 0x31, 0x32, 0x33,
        0x34, 0x35, 0x37, 0x77, 0xA0, 0xA1, 0xA2, 0xA8, 0xA9, 0xAA };
    static const uint8_t nine[] = { 0x70, 0x71, 0x72, 0x73, 0xA4, 0xAC, 0xBA, 0xC2, 0xC4, 0xC5, 0xC6 };
    unsigned b;

    for (b = 0; b < 0x40; b++)
        ONE[b] = b % 8 < 4 ? 8 : b % 8 == 4 ? 1 : b % 8 == 5 ? 2 : 0;
    for (b = 0; b < sizeof modrm; b++)
        ONE[modrm[b]] = 8;
    for (b = 0; b < sizeof other / 2; b++)
        ONE[other[b][0]] = other[b][1];
    for (b = 0x70; b < 0x80; b++)
        ONE[b] = 4;
    for (b = 0xB0; b < 0xC0; b++)
        ONE[b] = b < 0xB8 ? 1 : 3;
    for (b = 0xE0; b < 0xE8; b++)
        ONE[b] = 1;
    memset(TWO, 8, sizeof TWO);
    for (b = 0; b < sizeof bare; b++)
        TWO[bare[b]] = 0;
    for (b = 0xC8; b < 0xD0; b++)
        TWO[b] = 0;
    for (b = 0x80; b < 0x90; b++)
        TWO[b] = 6;
    for (b = 0; b < sizeof nine; b++)
        TWO[nine[b]] = 9;
}

static const uint8_t *code;             /* the code, read as zeros past its end */
static size_t size, o, last[NS];
static buf st[NS];
static unsigned k;                      /* the instruction's length so far */

static unsigned at(size_t i)
{
    return i < size ? code[i] : 0;
}

/* Moves the next n bytes to stream s and gives the last. */
static unsigned part(int s, unsigned n)
{
    k += n;
    last[s] = st[s].n;
    for (unsigned i = 0; i < n; i++)
        put1(&st[s], at(o + i));
    o += n;
    return at(o - 1);
}

/* One instruction. Gives the stream of its call, conditional jump or
   RIP-relative field, and where it is from the instruction's start; -1 if
   it has none. */
static int walk(unsigned *field)
{
    unsigned op, p66 = 0, w = 0, m = 0, kind, two;
    int s = -1;

    k = 0;
    for (;;) {
        op = part(OP, 1);
        if (op != 0x66 && op != 0x67 && op != 0xF2 && op != 0xF3 && op != 0xF0 && op != 0x2E
            && op != 0x3E && op != 0x26 && op != 0x64 && op != 0x65 && op != 0x36)
            break;
        p66 |= op == 0x66;
    }
    if ((op & 0xF0) == 0x40) {
        w = op & 8;
        op = part(OP, 1);
    }
    two = op == 0x0F;
    if (two) {
        op = part(OP, 1);
        if (op == 0x38 || op == 0x3A) {
            kind = op == 0x3A ? 9 : 8;
            part(OP, 1);
        } else
            kind = TWO[op];
    } else
        kind = ONE[op];
    if (kind & 8) {
        unsigned mod, rm, stack = 0, lea = op == 0x8D;
        m = part(OP, 1);
        mod = m >> 6, rm = m & 7;
        if (mod != 3) {                 /* LEA's, and those off the stack pointer: apart */
            if (rm == 4) {
                unsigned base = part(SIB, 1) & 7;
                stack = base == 4;
                if (base == 5 && mod == 0)
                    part(D32, 4);
            } else if (mod == 0 && rm == 5) {
                *field = k, s = RIP;
                part(RIP, 4);
            }
            if (mod == 1)
                part(lea ? D8L : stack ? D8S : D8, 1);
            else if (mod == 2)
                part(lea ? D32L : stack ? D32S : D32, 4);
        }
    }
    kind &= 7;
    if (kind == 7)
        kind = m >> 3 & 6 ? 0 : op == 0xF6 ? 1 : 2;
    if (kind == 1)                      /* a shift's count apart */
        part((op & 0xFE) == 0xC0 ? ISH : I8, 1);
    else if (kind == 2)
        part(op == 0xC7 ? MOV : I32, p66 ? 2 : 4);
    else if (kind == 3)                 /* MOV r, imm apart */
        part(w ? I64 : IMR, w ? 8 : p66 ? 2 : 4);
    else if (kind == 4)
        part(J8, 1);
    else if (kind == 5) {
        *field = k, s = CALL;
        part(CALL, 4);
    } else if (kind == 6 && two) {
        *field = k, s = JCC;
        part(JCC, 4);
    } else if (kind == 6)
        part(JMP, 4);
    return s;
}

/* The fields of mem with its code at [lo, hi): the 21 u32s, the streams,
   then the data. Each instruction's OP bytes start at tokens[i] in the OP
   stream, which ends the list. */
static void split(buf *fields, const uint8_t *mem, size_t n, size_t lo, size_t hi, buf *tokens)
{
    if (!(lo <= hi && hi <= n && n <= 0xFFFFFF00u))
        die("invalid image size or code bounds");
    code = mem + lo, size = hi - lo, o = 0;
    for (int s = 0; s < NS; s++)
        st[s].n = 0;
    tokens->n = 0;
    while (o < size) {
        size_t start = o;
        unsigned field = 0;
        put4(tokens, (uint32_t)st[OP].n);
        int s = walk(&field);
        if (s >= 0 && start + k <= size)
            store4(st[s].b + last[s], (uint32_t)(load4(code + start + field) + lo + start + k));
    }
    put4(tokens, (uint32_t)st[OP].n);
    fields->n = 0;
    put4(fields, (uint32_t)lo);
    put4(fields, (uint32_t)hi);
    for (int i = 0; i < NS; i++)
        put4(fields, (uint32_t)st[ORDER[i]].n);
    for (int i = 0; i < NS; i++)
        put(fields, st[ORDER[i]].b, st[ORDER[i]].n);
    put(fields, mem, lo);
    put(fields, mem + hi, n - hi);
}

/* ---- The opcode dictionary, as opcodes.py encodes it ---- */

typedef struct {
    uint64_t key;                       /* the length, then up to seven bytes */
    uint32_t count, first;
    int32_t row;
} form;

static form *forms;

static int by_count(const void *a, const void *b)
{
    const form *x = &forms[*(const uint32_t *)a], *y = &forms[*(const uint32_t *)b];
    if (x->count != y->count)
        return x->count > y->count ? -1 : 1;
    return x->first < y->first ? -1 : x->first > y->first;
}

static int by_length(const void *a, const void *b)
{
    uint64_t x = forms[*(const uint32_t *)a].key, y = forms[*(const uint32_t *)b].key;
    if (x >> 56 != y >> 56)
        return x >> 56 < y >> 56 ? -1 : 1;
    for (int i = 0; i < 56; i += 8)
        if ((x >> i & 255) != (y >> i & 255))
            return (x >> i & 255) < (y >> i & 255) ? -1 : 1;
    return 0;
}

static void literals(buf *data, buf *pending)
{
    for (size_t i = 0; i < pending->n; i += 255) {
        size_t chunk = pending->n - i < 255 ? pending->n - i : 255;
        put1(data, 0);
        put1(data, (unsigned)chunk);
        put(data, pending->b + i, chunk);
    }
    pending->n = 0;
}

/* The forms seen at least twice, the 8,160 most common at most, as a table
   of rows, then a tag for each instruction whose form has a row and the
   others' bytes as literals. Empty when no form repeats. */
static void encode(buf *data, const uint8_t *ops, const buf *tokens)
{
    size_t count = tokens->n / 4 - 1, slots = 1, nforms = 0;
    uint32_t *slot, *rank;

    data->n = 0;
    while (slots < 2 * count + 2)
        slots *= 2;
    slot = malloc(slots * sizeof *slot);
    forms = malloc((count + 1) * sizeof *forms);
    rank = malloc((count + 1) * sizeof *rank);
    if (!slot || !forms || !rank)
        die("out of memory");
    memset(slot, 0xFF, slots * sizeof *slot);
    uint64_t *keys = malloc((count + 1) * sizeof *keys);
    if (!keys)
        die("out of memory");
    for (size_t i = 0; i < count; i++) {
        uint32_t from = load4(tokens->b + 4 * i), len = load4(tokens->b + 4 * i + 4) - from;
        keys[i] = 0;
        if (len > 7)
            continue;
        uint64_t key = (uint64_t)len << 56;
        for (uint32_t j = 0; j < len; j++)
            key |= (uint64_t)ops[from + j] << 8 * j;
        keys[i] = key;
        size_t h = (size_t)(key * 0x9E3779B97F4A7C15u >> 20) & (slots - 1);
        while (slot[h] != UINT32_MAX && forms[slot[h]].key != key)
            h = (h + 1) & (slots - 1);
        if (slot[h] == UINT32_MAX) {
            slot[h] = (uint32_t)nforms;
            forms[nforms] = (form){ key, 0, (uint32_t)i, -1 };
            rank[nforms] = (uint32_t)nforms;
            nforms++;
        }
        forms[slot[h]].count++;
    }
    qsort(rank, nforms, sizeof *rank, by_count);
    size_t rows = 0;
    while (rows < nforms && rows < 8160 && forms[rank[rows]].count >= 2)
        rows++;
    if (rows) {
        size_t s = 255 - (rows - 1) / 255;
        size_t shorts = s < rows ? s : rows;
        qsort(rank, shorts, sizeof *rank, by_length);
        qsort(rank + shorts, rows - shorts, sizeof *rank, by_length);
        put1(data, (unsigned)(rows & 255));
        put1(data, (unsigned)(rows >> 8));
        for (int lane = 0; lane < 8; lane++) {   /* each column delta coded */
            unsigned before = 0;
            for (size_t i = 0; i < rows; i++) {
                uint64_t key = forms[rank[i]].key;
                unsigned b = lane ? key >> 8 * (lane - 1) & 255 : key >> 56;
                put1(data, i ? (b - before) & 255 : b);
                before = b;
            }
        }
        for (size_t i = 0; i < rows; i++)
            forms[rank[i]].row = (int32_t)i;
        buf pending = { 0 };
        for (size_t i = 0; i < count; i++) {
            uint32_t from = load4(tokens->b + 4 * i), len = load4(tokens->b + 4 * i + 4) - from;
            int32_t row = -1;
            if (len <= 7) {
                size_t h = (size_t)(keys[i] * 0x9E3779B97F4A7C15u >> 20) & (slots - 1);
                while (forms[slot[h]].key != keys[i])
                    h = (h + 1) & (slots - 1);
                row = forms[slot[h]].row;
            }
            if (row < 0) {
                put(&pending, ops + from, len);
                continue;
            }
            literals(data, &pending);
            if ((size_t)row < s)
                put1(data, (unsigned)row + 1);
            else {
                put1(data, (unsigned)(s + 1 + (row - s) % (255 - s)));
                put1(data, (unsigned)((row - s) / (255 - s)));
            }
        }
        literals(data, &pending);
        free(pending.b);
    }
    free(slot);
    free(forms);
    free(rank);
    free(keys);
}

/* ---- LZMA: raw LZMA1 with an end marker, one encoder setting ---- */

/* An 8 MiB window (the readers'), BT3, depth 256, nice length 64: the
   setting measured best for this encoder on Pinion's core (README.md). */
#ifdef HEDDLE_LIBLZMA
static void compress(buf *out, const uint8_t *in, size_t n, unsigned lc, unsigned lp, unsigned pb)
{
    lzma_options_lzma opt;
    lzma_lzma_preset(&opt, 9 | LZMA_PRESET_EXTREME);
    opt.dict_size = 1u << 23, opt.mf = LZMA_MF_BT3, opt.depth = 256, opt.nice_len = 64;
    opt.lc = lc, opt.lp = lp, opt.pb = pb;
    lzma_filter filters[] = { { LZMA_FILTER_LZMA1, &opt }, { LZMA_VLI_UNKNOWN, NULL } };
    size_t pos = 0, bound = lzma_stream_buffer_bound(n);
    out->n = 0;
    grow(out, bound);
    if (lzma_raw_buffer_encode(filters, NULL, in, n, out->b, &pos, bound) != LZMA_OK)
        die("liblzma failed");
    out->n = pos;
}
#else
typedef struct {
    ISeqInStream in;
    const uint8_t *p;
    size_t n;
} reader;

typedef struct {
    ISeqOutStream out;
    buf *to;
} writer;

static SRes read_some(void *p, void *to, size_t *n)
{
    reader *r = p;
    if (*n > r->n)
        *n = r->n;
    if (*n)
        memcpy(to, r->p, *n);
    r->p += *n, r->n -= *n;
    return SZ_OK;
}

static size_t write_some(void *p, const void *from, size_t n)
{
    put(((writer *)p)->to, from, n);
    return n;
}

static void *alloc(void *p, size_t n) { (void)p; return malloc(n); }
static void release(void *p, void *q) { (void)p; free(q); }
static ISzAlloc heap = { alloc, release };

static void compress(buf *out, const uint8_t *in, size_t n, unsigned lc, unsigned lp, unsigned pb)
{
    CLzmaEncProps props;
    LzmaEncProps_Init(&props);
    props.level = 9, props.dictSize = 1u << 23, props.algo = 1, props.btMode = 1;
    props.numHashBytes = 3, props.mc = 256, props.fb = 64, props.numThreads = 1;
    props.lc = (int)lc, props.lp = (int)lp, props.pb = (int)pb, props.writeEndMark = 1;
    reader r = { { read_some }, in, n };
    writer w = { { write_some }, out };
    CLzmaEncHandle e = LzmaEnc_Create(&heap);
    out->n = 0;
    if (!e || LzmaEnc_SetProps(e, &props) != SZ_OK
        || LzmaEnc_Encode(e, &w.out, &r.in, NULL, &heap, &heap) != SZ_OK)
        die("the LZMA encoder failed");
    LzmaEnc_Destroy(e, &heap, &heap);
}
#endif

/* ---- The member ---- */

static uint32_t crc32(uint32_t crc, const uint8_t *p, size_t n)
{
    static uint32_t table[256];
    if (!table[1])
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int j = 0; j < 8; j++)
                c = c & 1 ? 0xEDB88320u ^ c >> 1 : c >> 1;
            table[i] = c;
        }
    crc = ~crc;
    while (n--)
        crc = table[(crc ^ *p++) & 255] ^ crc >> 8;
    return ~crc;
}

/* Each block its own LZMA context: small operands, larger immediates and
   targets, opcodes, the data. */
static const uint8_t GROUPS[4][4] = { { 9, 0, 0, 2 }, { 18, 1, 2, 2 }, { 19, 3, 0, 0 }, { NS + 1, 0, 1, 2 } };

/* The blocks of fields into payload, or 0 when one is over the limit. *xor
   says whether the JCC stream's XOR made its block smaller. */
static int grouped(buf *payload, const buf *fields, size_t n, const uint8_t *ops, const buf *tokens, int *xor)
{
    const uint8_t *f = fields->b;
    size_t head = 4 * (NS + 2), offset[NS + 2], start = 0;
    buf block = { 0 }, other = { 0 }, xored = { 0 }, dict = { 0 };

    offset[0] = head;
    for (int i = 0; i < NS; i++)
        offset[i + 1] = offset[i] + load4(f + 8 + 4 * i);
    offset[NS + 1] = fields->n;
    payload->n = 0;
    *xor = 0;
    for (int g = 0; g < 4; g++) {
        unsigned stop = GROUPS[g][0], lc = GROUPS[g][1], lp = GROUPS[g][2], pb = GROUPS[g][3];
        size_t end = offset[stop];
        unsigned props = (pb * 5 + lp) * 9 + lc;
        if (start < end) {
            compress(&block, f + start, end - start, lc, lp, pb);
            size_t jcc = offset[JCC_AT];
            if (start <= jcc && jcc < end && offset[JCC_AT + 1] > jcc) {
                xored.n = 0;           /* each u32 XORed with the one before */
                put(&xored, f + start, end - start);
                uint32_t prev = 0;
                for (size_t p = jcc - start; p < offset[JCC_AT + 1] - start; p += 4) {
                    uint32_t v = load4(xored.b + p);
                    store4(xored.b + p, v ^ prev);
                    prev = v;
                }
                compress(&other, xored.b, xored.n, lc, lp, pb);
                if (other.n < block.n) {
                    buf t = block;
                    block = other, other = t, *xor = 1;
                }
            }
            if (stop == OP + 1 && tokens->n > 4) {
                encode(&dict, ops, tokens);
                if (dict.n && dict.n <= n) {
                    other.n = 0;
                    compress(&other, dict.b, dict.n, lc, lp, pb);
                    if (4 + other.n < block.n) {
                        block.n = 0;
                        put4(&block, (uint32_t)dict.n);
                        put(&block, other.b, other.n);
                        props = 253;
                    }
                }
            }
            if (block.n > LIMIT)
                return 0;
            put1(payload, props);
            put4(payload, (uint32_t)block.n);
            put4(payload, (uint32_t)(end - start));
            put(payload, block.b, block.n);
        }
        start = end;
    }
    free(block.b), free(other.b), free(xored.b), free(dict.b);
    return 1;
}

static void member(buf *out, const buf *fields, size_t n, const uint8_t *ops, const buf *tokens)
{
    buf payload = { 0 }, blocks = { 0 };
    const char *magic = "HDS2";
    int xor;

    if (n > 0xFFFFFF00u || fields->n > LIMIT)
        die("an image is at most 0xffffff00 bytes");
    compress(&payload, fields->b, fields->n, 3, 0, 2);
    if (fields->n >= 1024                /* small members cannot recover the block headers */
        && grouped(&blocks, fields, n, ops, tokens, &xor) && blocks.n < payload.n) {
        buf t = payload;
        payload = blocks, blocks = t;
        magic = xor ? "HDX2" : "HDB2";
    }
    if (payload.n > LIMIT - 16)
        die("a member is at most 4 GiB - 1 bytes, the C reader's limit");
    out->n = 0;
    put(out, magic, 4);
    put4(out, (uint32_t)n);
    put4(out, (uint32_t)fields->n);
    put4(out, crc32(crc32(0, out->b, 12), payload.b, payload.n));
    put(out, payload.b, payload.n);
    free(payload.b), free(blocks.b);
}

/* The member of mem, read back by the C reader before it is given. */
static void pack(buf *out, const uint8_t *mem, size_t n, size_t lo, size_t hi)
{
    buf fields = { 0 }, tokens = { 0 };
    split(&fields, mem, n, lo, hi, &tokens);
    member(out, &fields, n, st[OP].b, &tokens);
    uint8_t *image = malloc(n + 1), *scratch = malloc(fields.n);
    if (!image || !scratch)
        die("out of memory");
    if (heddle_decode(out->b, (uint32_t)out->n, image, (uint32_t)n, scratch, (uint32_t)fields.n)
        || memcmp(image, mem, n))
        die("the member does not make the input again");
    free(image), free(scratch), free(fields.b), free(tokens.b);
}

/* ---- Files ---- */

static uint64_t rd(const uint8_t *d, size_t n, uint64_t off, int bytes)
{
    uint64_t v = 0;
    if (off > n || (uint64_t)bytes > n - off)
        die("invalid executable headers");
    for (int i = bytes; i--;)
        v = v << 8 | d[off + i];
    return v;
}

static int has(const uint8_t *d, size_t n, uint64_t off, const char *what, size_t len)
{
    return off <= n && len <= n - off && !memcmp(d + off, what, len);
}

/* File offsets of .text in a little-endian x86-64 ELF or PE. */
static void code_bounds(const uint8_t *d, size_t n, size_t *lo, size_t *hi)
{
    uint64_t offset = 0, length = 0;

    if (has(d, n, 0, "\x7f" "ELF\x02\x01", 6) && rd(d, n, 18, 2) == 62) {
        uint64_t table = rd(d, n, 40, 8), step = rd(d, n, 58, 2), count = rd(d, n, 60, 2);
        uint64_t names = rd(d, n, 62, 2), i;
        if (step < 64 || names >= count || table > n || step * count > n - table)
            die("invalid ELF section table");
        uint64_t start = rd(d, n, table + step * names + 24, 8);
        uint64_t avail = rd(d, n, table + step * names + 32, 8);
        avail = start > n ? 0 : avail < n - start ? avail : n - start;
        for (i = 0; i < count; i++) {
            uint64_t entry = table + i * step, name = rd(d, n, entry, 4);
            if (name <= avail && 6 <= avail - name && !memcmp(d + start + name, ".text", 6)
                && rd(d, n, entry + 4, 4) == 1 && rd(d, n, entry + 8, 8) & 4) {
                offset = rd(d, n, entry + 24, 8), length = rd(d, n, entry + 32, 8);
                break;
            }
        }
        if (i == count)
            die("missing executable .text");
    } else if (has(d, n, 0, "MZ", 2)) {
        uint64_t pe = rd(d, n, 60, 4), i, count;
        if (!has(d, n, pe, "PE\0\0\x64\x86", 6))
            die("expected x86-64 PE");
        count = rd(d, n, pe + 6, 2);
        uint64_t optional = rd(d, n, pe + 20, 2);
        for (i = 0; i < count; i++) {
            uint64_t entry = pe + 24 + optional + i * 40;
            if (has(d, n, entry, ".text\0\0\0", 8)) {
                uint64_t virtual = rd(d, n, entry + 8, 4), stored = rd(d, n, entry + 16, 4);
                offset = rd(d, n, entry + 20, 4), length = virtual < stored ? virtual : stored;
                break;
            }
        }
        if (i == count)
            die("missing PE .text");
    } else
        die("supply --code LO HI for a raw image");
    if (offset > n || length > n - offset)
        die("code runs past end of file");
    *lo = (size_t)offset, *hi = (size_t)(offset + length);
}

static void fail(const char *path)
{
    fprintf(stderr, "heddle: %s: %s\n", path, strerror(errno));
    exit(1);
}

static void slurp(buf *x, const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        fail(path);
    for (;;) {
        grow(x, 1 << 16);
        size_t got = fread(x->b + x->n, 1, x->cap - x->n, f);
        x->n += got;
        if (!got)
            break;
    }
    if (ferror(f))
        fail(path);
    fclose(f);
}

static void spill(const char *path, const uint8_t *p, size_t n)
{
    FILE *f = fopen(path, "wb");
    if (!f || fwrite(p, 1, n, f) != n || fclose(f))
        fail(path);
}

/* BCJ2 with three LZMA streams, as 7-Zip packs it, if 7z or 7zz runs. */
static long long bcj2(const uint8_t *d, size_t n)
{
    static const char *const sevens[] = { "7z", "7zz" };
    char dir[4096], file[4200], arc[4200], cmd[9000], line[512];
    long long packed = -1;
#ifdef _WIN32
    const char *null = "NUL", *c = "";
    char *name = _tempnam(NULL, "heddle");
    if (!name || snprintf(dir, sizeof dir, "%s", name) >= (int)sizeof dir || _mkdir(dir))
        return free(name), -1;
    free(name);
    snprintf(file, sizeof file, "%s\\bin", dir);
    snprintf(arc, sizeof arc, "%s\\a.7z", dir);
#else
    const char *tmp = getenv("TMPDIR"), *null = "/dev/null", *c = "LC_ALL=C ";
    snprintf(dir, sizeof dir, "%s/heddle-XXXXXX", tmp && *tmp ? tmp : "/tmp");
    if (!mkdtemp(dir))
        return -1;
    snprintf(file, sizeof file, "%s/bin", dir);
    snprintf(arc, sizeof arc, "%s/a.7z", dir);
#endif
    spill(file, d, n);
    for (int i = 0; i < 2 && packed < 0; i++) {
        snprintf(cmd, sizeof cmd, "%s a -m0=BCJ2 -m1=LZMA:d=8m:fb=96 -m2=LZMA:d=8m:fb=96 "
                 "-m3=LZMA:d=8m:fb=96 -mb0:1 -mb0s1:2 -mb0s2:3 -sibin \"%s\" <\"%s\" >%s 2>&1",
                 sevens[i], arc, file, null);
        if (system(cmd)) {
            remove(arc);
            continue;
        }
        snprintf(cmd, sizeof cmd, "%s%s l -slt \"%s\" 2>%s", c, sevens[i], arc, null);
        FILE *p = popen(cmd, "r");
        while (p && fgets(line, sizeof line, p))
            if (packed < 0 && !strncmp(line, "Packed Size = ", 14))
                packed = strtoll(line + 14, NULL, 10);
        if (p)
            pclose(p);
        remove(arc);
    }
    remove(file);
#ifdef _WIN32
    _rmdir(dir);
#else
    rmdir(dir);
#endif
    return packed;
}

static size_t lzma_size(const uint8_t *d, size_t n, int x86)
{
    buf in = { 0 }, out = { 0 };
    put(&in, d, n);
    if (x86) {
        UInt32 state;
        x86_Convert_Init(state);
        x86_Convert(in.b, n, 0, &state, 1);
    }
    compress(&out, in.b, n, 3, 0, 2);
    free(in.b), free(out.b);
    return out.n;
}

/* ---- The command ---- */

static void usage(void)
{
    fputs("usage: heddle pack INPUT OUTPUT [--code LO HI]\n"
          "       heddle unpack INPUT OUTPUT\n"
          "       heddle compare INPUT [--code LO HI]\n", stderr);
    exit(2);
}

/* A number as Python's int(s, 0) reads it: decimal, 0x, 0o or 0b. */
static uint64_t number(const char *s)
{
    unsigned base = 10;
    uint64_t v = 0;
    int digits = 0;

    if (s[0] == '0' && s[1] && strchr("xXoObB", s[1]))
        base = strchr("xX", s[1]) ? 16 : strchr("oO", s[1]) ? 8 : 2, s += 2;
    else if (s[0] == '0' && s[1])
        usage();
    for (; *s; s++) {
        unsigned d = *s >= '0' && *s <= '9' ? (unsigned)(*s - '0')
                   : *s >= 'a' && *s <= 'f' ? (unsigned)(*s - 'a' + 10)
                   : *s >= 'A' && *s <= 'F' ? (unsigned)(*s - 'A' + 10) : 99;
        if (*s == '_' && digits)
            continue;
        if (d >= base || v > (UINT64_MAX - d) / base)
            usage();
        v = v * base + d, digits++;
    }
    if (!digits)
        usage();
    return v;
}

int main(int argc, char **argv)
{
    const char *cmd = argc > 1 ? argv[1] : "", *path[2] = { 0 };
    int paths = 0, bounded = 0, want;
    uint64_t lo = 0, hi = 0;

    want = !strcmp(cmd, "compare") ? 1 : !strcmp(cmd, "pack") || !strcmp(cmd, "unpack") ? 2 : 0;
    if (!want)
        usage();
    for (int i = 2; i < argc; i++)
        if (!strcmp(argv[i], "--code") && strcmp(cmd, "unpack") && i + 2 < argc && !bounded)
            lo = number(argv[i + 1]), hi = number(argv[i + 2]), i += 2, bounded = 1;
        else if (argv[i][0] == '-' && argv[i][1])
            usage();
        else if (paths < want)
            path[paths++] = argv[i];
        else
            usage();
    if (paths != want)
        usage();

    tables();
    buf in = { 0 }, out = { 0 };
    slurp(&in, path[0]);
    if (!strcmp(cmd, "unpack")) {
        if (in.n < 16 || in.n > LIMIT
            || (memcmp(in.b, "HDL1", 4) && memcmp(in.b, "HDS2", 4) && memcmp(in.b, "HDB2", 4)
                && memcmp(in.b, "HDX2", 4))
            || load4(in.b + 4) > 256u << 20 || load4(in.b + 8) > 256u << 20)
            die("invalid header or size limit exceeded");
        uint32_t n = load4(in.b + 4), need = load4(in.b + 8);
        uint8_t *image = malloc(n + 1), *scratch = malloc(need + 1);
        if (!image || !scratch)
            die("out of memory");
        if (heddle_decode(in.b, (uint32_t)in.n, image, n, scratch, need))
            die("invalid member");
        spill(path[1], image, n);
        free(image), free(scratch), free(in.b);
        return 0;
    }
    size_t l, h;
    if (bounded) {
        if (lo > hi || hi > in.n)
            die("invalid image size or code bounds");
        l = (size_t)lo, h = (size_t)hi;
    } else
        code_bounds(in.b, in.n, &l, &h);
    pack(&out, in.b, in.n, l, h);
    if (!strcmp(cmd, "pack"))
        spill(path[1], out.b, out.n);
    else {
        long long seven = bcj2(in.b, in.n);
        printf("input\tLZMA\tx86 BCJ\tBCJ2\theddle\n%llu\t%llu\t%llu\t", (unsigned long long)in.n,
               (unsigned long long)lzma_size(in.b, in.n, 0), (unsigned long long)lzma_size(in.b, in.n, 1));
        if (seven < 0)
            printf("n/a");
        else
            printf("%lld", seven);
        printf("\t%llu\n", (unsigned long long)(out.n - 16));
    }
    free(in.b), free(out.b);
    return 0;
}
