# Head protocol tools

The robot's head may be the **Tab5**, or a **tablet face with a separate neck controller**. Everything
that talks to the head speaks one protocol, specified in
[`notes/design/head-protocol.md`](../../notes/design/head-protocol.md). This folder holds the tools
that keep both heads, and every sender, to it.

| file | what |
|---|---|
| `head_protocol.py` | the protocol as data: messages, vocabulary, leases, reply formats. One copy, shared by the rest |
| `conformance.py` | checks a head against the protocol |
| `fake_head.py` | a reference head on `127.0.0.1`, of any kind, for developing a sender with no head |
| `tests/` | the suite's own tests, plus a check of the protocol against the Tab5's source (run in CI) |

Check a head (read-only, safe on a robot):

```bash
python3 software/head-protocol/conformance.py opendubs-tab5.local
```

A tablet head is two boxes: `conformance.py <face-host> --neck <neck-host>`. `--active` also changes
the face; `--motion` also moves the head and watches its angle over time. Both need
`--at-the-robot` on anything but `127.0.0.1`, and `--motion` needs a person watching.

Run a reference head and talk to it:

```bash
python3 software/head-protocol/fake_head.py --kind tab5          # or legacy-tab5, tablet-face, neck
python3 software/head-protocol/conformance.py 127.0.0.1 --active --motion
```

Standard library only; Python 3.9 or later.
