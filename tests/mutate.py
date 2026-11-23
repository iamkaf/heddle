#!/usr/bin/env python3
"""Remove each reader check in turn and require test.py to fail: every
check has a test that fails for the reason it names. Works on a copy of
the checkout; about a minute and a half."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

C = {
    "first byte": ("    if (code)\n        return 1;\n#endif\n    for (i = 0; i < 4; i++)",
                   "#endif\n    for (i = 0; i < 4; i++)"),
    "literal past the end": ("            if (out == size)\n                return 1;\n#endif\n            /* A literal.",
                             "#endif\n            /* A literal."),
    "rep past the end": ("if (out - 1 >= size - 1)", "if (!out)"),
    "window": (" || d >> 23", ""),
    "end marker": ("if (d == ~0u && out == size)", "if (out == size)"),
    "match past the end": ("        if (l + 2 > size - out)\n            return 1;\n        for (l += 2; l--; out++)",
                           "        for (l += 2; l-- && out < size; out++)"),
    "final code": ("return code | (inpos != n);", "return inpos != n;"),
    "extent": ("return code | (inpos != n);", "return code | (inpos > n);"),
    "short block": ("    if (n < 2)\n        return 1;", ""),
    "no rows": ("if (!count || count > 65025 || pos > n)", "if (count > 65025 || pos > n)"),
    "too many rows": ("if (!count || count > 65025 || pos > n)", "if (!count || pos > n)"),
    "rows past the block": ("if (!count || count > 65025 || pos > n)", "if (!count || count > 65025)"),
    "truncated tag": ("            if (pos == n)\n                return 1;\n            extra", "            extra"),
    "literal span": ("            if (length > n - pos)\n                return 1;", ""),
    "index": ("            if (index >= count)\n                return 1;", ""),
    "row length": ("            if (length > 7)\n                return 1;", ""),
    "zero length": ("if (!length || length > size - out)", "if (length > size - out)"),
    "overrun": ("if (!length || length > size - out)", "if (!length)"),
    "incomplete": ("    return out != size;\n}", "    return 0;\n}"),
}
PY = {
    "short block": ('    if len(data) < 2:\n        raise ValueError("invalid opcode dictionary")\n', ""),
    "rows": ("if not 0 < count <= 65025 or pos > len(data):", "if not 0 < count or pos > len(data):"),
    "truncated tag": ('                raise ValueError("truncated opcode tag")', "                pass"),
    "index": ('                raise ValueError("invalid opcode index")', "                pass"),
    "row length": ('                raise ValueError("invalid opcode length")', "                pass"),
    "span": ("        if not length or start + length > len(data) or length > size - len(result):",
             "        if not length or length > size - len(result):"),
    "incomplete": ('        raise ValueError("incomplete opcode stream")', "        pass"),
}

here = Path(__file__).resolve().parent.parent
survivors = 0
with tempfile.TemporaryDirectory() as tmp:
    for name in ("heddle.py", "opcodes.py", "split.py", "unpack.c", "unsplit.c", "heddle.h", "test.py"):
        shutil.copy(here / name, tmp)
    shutil.copytree(here / "tests", Path(tmp) / "tests")
    # The copy runs elsewhere: inputs by their full paths, FILE:LO:HI kept.
    inputs = []
    for name in sys.argv[1:]:
        parts = name.rsplit(":", 2)
        path, bounds = (parts[0], parts[1:]) if len(parts) == 3 else (name, [])
        inputs.append(":".join([str(Path(path).resolve()), *bounds]))

    def test():
        return subprocess.run([sys.executable, "test.py", *inputs], cwd=tmp, capture_output=True,
                              env=dict(os.environ, CC=os.environ.get("CC", "clang")))

    first = test()                      # unchanged, it passes, so a failure below is the mutation's
    if first.returncode:
        sys.exit("test.py fails unchanged:\n" + first.stderr.decode()[-2000:])
    for path, mutations in (("unpack.c", C), ("opcodes.py", PY)):
        source = (Path(tmp) / path).read_text()
        for what, (old, new) in mutations.items():
            assert source.count(old) == 1, what
            (Path(tmp) / path).write_text(source.replace(old, new))
            run = test()
            print(f"{path}: {what}: {'caught' if run.returncode else 'NOT CAUGHT'}")
            survivors += not run.returncode
        (Path(tmp) / path).write_text(source)
sys.exit(f"{survivors} checks without a test" if survivors else 0)
