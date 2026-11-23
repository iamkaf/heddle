"""Encode the opcode, prefix and ModRM bytes of common instruction forms."""
from collections import Counter
import struct


def encode(tokens, cap=8160):
    counts = Counter(t for t in tokens if len(t) <= 7)
    forms = [t for t, freq in counts.most_common(cap) if freq >= 2]
    if not forms:
        return b""
    count = len(forms)
    short = 255 - (count - 1) // 255
    def by_length(form):
        return len(form), form

    forms = sorted(forms[:short], key=by_length) + sorted(forms[short:], key=by_length)
    tags = {}
    data = bytearray(struct.pack("<H", count))
    for i, form in enumerate(forms):
        data.extend(bytes([len(form)]) + form.ljust(7, b"\0"))
        if i < short:
            tags[form] = bytes([i + 1])
        else:
            offset, page = divmod(i - short, 255 - short)
            tags[form] = bytes([short + 1 + page, offset])
    # Each dictionary column repeats across adjacent, sorted forms.
    table = data[2:]
    del data[2:]
    for lane in range(8):
        column = table[lane::8]
        data.append(column[0])
        data.extend((b-a) & 255 for a, b in zip(column, column[1:]))
    literals = bytearray()
    for t in tokens:
        tag = tags.get(t)
        if tag is None:
            literals.extend(t)
            continue
        emit_literals(data, literals)
        data.extend(tag)
    emit_literals(data, literals)
    return data


def emit_literals(data, literals):
    for i in range(0, len(literals), 255):
        chunk = literals[i:i+255]
        data.extend((0, len(chunk)))
        data.extend(chunk)
    literals.clear()


def decode(data, size):
    if len(data) < 2:
        raise ValueError("invalid opcode dictionary")
    count, = struct.unpack_from("<H", data)
    pos = 2 + 8 * count
    if not 0 < count <= 65025 or pos > len(data):
        raise ValueError("invalid opcode dictionary")
    short = 255 - (count - 1) // 255
    rows = bytearray(data)
    for lane in range(8):
        value = 0
        for i in range(count):
            value = (value + data[2 + lane * count + i]) & 255
            rows[2 + 8 * i + lane] = value
    data = rows
    result = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag == 0 or tag > short:
            if pos == len(data):
                raise ValueError("truncated opcode tag")
            extra = data[pos]
            pos += 1
        if tag == 0:
            start, length = pos, extra
            pos += length
        else:
            index = tag - 1
            if tag > short:
                page = index - short
                index = short + extra * (255 - short) + page
            if index >= count:
                raise ValueError("invalid opcode index")
            start = 3 + 8 * index
            length = data[start-1]
            if length > 7:
                raise ValueError("invalid opcode length")
        if not length or start + length > len(data) or length > size - len(result):
            raise ValueError("invalid opcode span")
        result.extend(data[start:start+length])
    if len(result) != size:
        raise ValueError("incomplete opcode stream")
    return result
