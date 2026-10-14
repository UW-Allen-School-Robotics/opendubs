"""Cut a streamed reply into sentences, so speech can start before the model has finished.

Time to first sentence, not tokens per second, is what a listener waits on (voice note §2.2). A
sentence ends at . ! or ? followed by whitespace, or at a line break. A run-on with no end is cut
at the last comma or space after MAX_CHARS, so one long sentence cannot hold the voice back.
"""
from __future__ import annotations

import re

END = re.compile(r"[.!?]+[\"')\]]*\s+|\n+")
MAX_CHARS = 180
MIN_CHARS = 2          # "OK." is a sentence; "." alone is not


class Splitter:
    def __init__(self) -> None:
        self.buf = ""

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out = []
        while True:
            m = END.search(self.buf)
            if m:
                cut = m.end()
            elif len(self.buf) > MAX_CHARS:
                head = self.buf[:MAX_CHARS]
                comma, space = head.rfind(", "), head.rfind(" ")
                cut = comma + 2 if comma > 0 else space + 1 if space > 0 else MAX_CHARS
            else:
                break
            s, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if len(s) >= MIN_CHARS:
                out.append(s)
        return out

    def flush(self) -> list[str]:
        s, self.buf = self.buf.strip(), ""
        return [s] if len(s) >= MIN_CHARS else []
