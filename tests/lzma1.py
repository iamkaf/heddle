"""A literal-minded raw LZMA1 encoder for tests: it writes exactly the
symbols it is told to, including ones a real encoder never would (a match
past the end, an end marker with a long length, no end marker at all).
Its probability layout and state rules mirror unpack.c."""

IS_MATCH, IS_REP, IS_REP_G0, IS_REP_G1, IS_REP_G2 = 0, 192, 204, 216, 228
IS_REP0_LONG, POS_SLOT, POS_SPEC, ALIGN, LEN, REP_LEN, LIT = 240, 432, 688, 803, 819, 1333, 1847


class Encoder:
    def __init__(self, lc=3, lp=0, pb=2):
        self.lc, self.lp, self.pb = lc, lp, pb
        self.p = [1024] * (LIT + (0x300 << lc + lp))
        self.low, self.range, self.cache, self.cache_size = 0, 0xFFFFFFFF, 0, 1
        self.out = bytearray()
        self.data = bytearray()
        self.state, self.rep = 0, [0, 0, 0, 0]

    def _shift_low(self):
        if self.low < 0xFF000000 or self.low >= 1 << 32:
            carry, temp = self.low >> 32, self.cache
            while True:
                self.out.append((temp + carry) & 0xFF)
                temp = 0xFF
                self.cache_size -= 1
                if not self.cache_size:
                    break
            self.cache = (self.low >> 24) & 0xFF
        self.cache_size += 1
        self.low = (self.low & 0x00FFFFFF) << 8

    def _norm(self):
        while self.range < 1 << 24:
            self.range <<= 8
            self._shift_low()

    def _bit(self, i, b):
        prob = self.p[i]
        bound = (self.range >> 11) * prob
        if b:
            self.low += bound
            self.range -= bound
            self.p[i] = prob - (prob >> 5)
        else:
            self.range = bound
            self.p[i] = prob + ((2048 - prob) >> 5)
        self._norm()

    def _tree(self, q, n, v):
        m = 1
        for i in reversed(range(n)):
            b = v >> i & 1
            self._bit(q + m, b)
            m = m * 2 + b

    def _rtree(self, q, n, v):
        m = 1
        for i in range(n):
            b = v >> i & 1
            self._bit(q + m, b)
            m = m * 2 + b

    def _len(self, q, l, ps):
        if l < 8:
            self._bit(q, 0)
            self._tree(q + 2 + ps * 8, 3, l)
        elif l < 16:
            self._bit(q, 1)
            self._bit(q + 1, 0)
            self._tree(q + 130 + ps * 8, 3, l - 8)
        else:
            self._bit(q, 1)
            self._bit(q + 1, 1)
            self._tree(q + 258, 8, l - 16)

    def _ps(self):
        return len(self.data) & ((1 << self.pb) - 1)

    def _copy(self, d, length):
        for _ in range(length):
            self.data.append(self.data[-d - 1] if d < len(self.data) else 0)

    def literal(self, byte):
        out = len(self.data)
        self._bit(IS_MATCH + self.state * 16 + self._ps(), 0)
        prev = self.data[-1] if out else 0
        q = LIT + 0x300 * (((out & ((1 << self.lp) - 1)) << self.lc) + (prev >> (8 - self.lc)))
        sym, offs = 1, 0x100 if self.state >= 7 else 0
        match = self.data[out - self.rep[0] - 1] if offs else 0
        for i in reversed(range(8)):
            b = byte >> i & 1
            match <<= 1
            was = offs
            offs &= match
            self._bit(q + offs + was + sym, b)
            sym = sym * 2 + b
            if not b:
                offs ^= was
        self.data.append(byte)
        self.state = 0 if self.state < 4 else self.state - 3 if self.state < 10 else self.state - 6

    def match(self, d, length):
        """A new distance d (0 is the previous byte), length 2 to 273."""
        ps = self._ps()
        self._bit(IS_MATCH + self.state * 16 + ps, 1)
        self._bit(IS_REP + self.state, 0)
        l = length - 2
        self._len(LEN, l, ps)
        self.state = 7 if self.state < 7 else 10
        if d < 4:
            slot = d
        else:
            n = d.bit_length() - 1
            slot = 2 * n + (d >> (n - 1) & 1)
        self._tree(POS_SLOT + min(l, 3) * 64, 6, slot)
        if slot >= 4:
            nd = (slot >> 1) - 1
            base = (2 | slot & 1) << nd
            extra = d - base
            if slot < 14:
                self._rtree(POS_SPEC + base - slot, nd, extra)
            else:
                for i in reversed(range(4, nd)):
                    self.range >>= 1
                    if d >> i & 1:
                        self.low += self.range
                    self._norm()
                self._rtree(ALIGN, 4, extra & 15)
        self.rep = [d] + self.rep[:3]
        self._copy(d, length)

    def rep_match(self, g, length):
        """The g-th latest distance again, length 2 to 273."""
        ps = self._ps()
        self._bit(IS_MATCH + self.state * 16 + ps, 1)
        self._bit(IS_REP + self.state, 1)
        if g == 0:
            self._bit(IS_REP_G0 + self.state, 0)
            self._bit(IS_REP0_LONG + self.state * 16 + ps, 1)
        else:
            self._bit(IS_REP_G0 + self.state, 1)
            self._bit(IS_REP_G1 + self.state, g > 1)
            if g > 1:
                self._bit(IS_REP_G2 + self.state, g > 2)
        self._len(REP_LEN, length - 2, ps)
        self.state = 8 if self.state < 7 else 11
        d = self.rep.pop(g)
        self.rep.insert(0, d)
        self._copy(d, length)

    def end(self, length=2):
        """The end marker: a new match at distance 2**32 - 1."""
        before = len(self.data)
        self.match(0xFFFFFFFF, length)
        del self.data[before:]

    def finish(self):
        for _ in range(5):
            self._shift_low()
        return bytes(self.out)


def literals(data, end=True):
    e = Encoder()
    for b in data:
        e.literal(b)
    if end:
        e.end()
    return e.finish()
