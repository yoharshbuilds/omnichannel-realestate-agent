"""Email addresses captured over a phone call arrive as speech-to-text, e.g.
"harsh dot shah at gmail dot com" or "h a r s h underscore 92 at the rate yahoo dot co dot in".
Normalise that into an address and reject anything that still doesn't look valid,
so the agent can ask the caller to spell it again instead of emailing the wrong person."""

import re

SPOKEN = [
    (r"\bat the rate( of)?\b", "@"),
    (r"\bat\b", "@"),
    (r"\b(dot|period|point)\b", "."),
    (r"\bunderscore\b", "_"),
    (r"\b(dash|hyphen|minus)\b", "-"),
]
DIGITS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
VALID = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")


def normalize_email(spoken: str | None) -> str | None:
    if not spoken:
        return None
    s = spoken.strip().lower()
    if VALID.match(s):
        return s
    for pattern, repl in SPOKEN:
        s = re.sub(pattern, f" {repl} ", s)
    s = " ".join(DIGITS.get(w, w) for w in s.split())
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"\.{2,}", ".", s).strip(".")
    return s if VALID.match(s) else None
