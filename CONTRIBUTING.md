# Contributing

Bug reports, fixes and measurements are welcome. Open an [issue](https://github.com/iamkaf/heddle/issues) for a bug or an idea, and a pull request for a change. Report a suspected vulnerability privately, as the [security policy](SECURITY.md) describes, not in a public issue.

## Keep the format

Version 2 of the wire format is frozen ([FORMAT.md](FORMAT.md)). A change may make the packer find smaller members, but every member it writes must still be one that today's readers accept, and the readers must keep accepting every member they accept now, version 1 included. A change that needs a different layout needs a new tag.

The C packer, `pack.c`, is held to the Python reference, `heddle.py` with `split.py` and `opcodes.py`. A change to how fields are split, how the opcode dictionary is chosen or how members are framed goes into both, and `tests/pack.py` checks that they still agree.

## Run the tests

Each script builds what it needs, with sanitizers, so there is nothing to build first:

```sh
CC=clang python3 test.py
CC=clang python3 tests/pack.py
python3 tests/mutate.py
```

`test.py` checks the decoder, `tests/pack.py` checks the C packer against the Python one, and `tests/mutate.py` checks that every reader check has a test that fails without it. If you add a check to a reader, add a test that fails without it and a mutation to `tests/mutate.py` that removes it. The README's [Run the tests](README.md#run-the-tests) section covers other inputs, macOS and MSVC.

CI runs all three on Linux with GCC and Clang, on an arm64 Mac and on Windows with MSVC. A pull request needs it to pass.

## Write the change

Keep the decoder small and freestanding: integer arithmetic, fixed static tables, no allocation. If a change affects its size or the compressed sizes in the README, re-measure them the way the README describes, and update the tables.

Make each commit one change. Write its subject as a short plain sentence, and use its body to say why the change is needed and anything a reader of the diff can't see.
