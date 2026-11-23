/* Run a batch of independent cases. Exact allocations give ASan red zones
   at the end of each caller buffer, including for undersized buffers. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../heddle.h"

static void read_bytes(void *p, size_t n)
{
    if (fread(p, 1, n, stdin) != n)
        exit(2);
}

int main(void)
{
    uint32_t h[4], index = 0;
    while (fread(h, sizeof h, 1, stdin) == 1) {
        uint8_t *in = malloc(h[0]), *dst = malloc(h[1]), *tmp = malloc(h[2]);
        uint8_t *expected = malloc(h[1]);
        if (!in || !dst || !tmp || !expected)
            return 2;
        read_bytes(in, h[0]);
        memset(tmp, 0xac, h[2]);
        int err = heddle_decode(in, h[0], dst, h[1], tmp, h[2]);
        if (h[3] == 0) {
            read_bytes(expected, h[1]);
            if (err || memcmp(expected, dst, h[1])) {
                fprintf(stderr, "case %u: %s\n", index, err ? "rejected" : "other bytes");
                return 3;
            }
            for (uint32_t i = 0; i < h[2]; i++)
                if (tmp[i]) {
                    fprintf(stderr, "case %u: scratch left\n", index);
                    return 4;
                }
        } else if (h[3] == 1 && !err) {
            fprintf(stderr, "case %u: accepted\n", index);
            return 5;
        }
        index++;
        free(in);
        free(dst);
        free(tmp);
        free(expected);
    }
    return 0;
}
