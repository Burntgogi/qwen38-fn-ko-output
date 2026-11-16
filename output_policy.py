"""Character classes used by the UTF-8 token policy."""

from bisect import bisect_right


# Pinned Script=Han table from regex 2025.10.23. Common punctuation stays usable.
HAN_RANGES = (
    (0x2E80, 0x2E99), (0x2E9B, 0x2EF3), (0x2F00, 0x2FD5), (0x3005, 0x3005),
    (0x3007, 0x3007), (0x3021, 0x3029), (0x3038, 0x303B), (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF), (0xF900, 0xFA6D), (0xFA70, 0xFAD9), (0x16FE2, 0x16FE3),
    (0x16FF0, 0x16FF6), (0x20000, 0x2A6DF), (0x2A700, 0x2B81D), (0x2B820, 0x2CEAD),
    (0x2CEB0, 0x2EBE0), (0x2EBF0, 0x2EE5D), (0x2F800, 0x2FA1D), (0x30000, 0x3134A),
    (0x31350, 0x33479),
)
HAN_STARTS = tuple(start for start, _ in HAN_RANGES)
TABLE_VERSION = "script-han/regex-2025.10.23"


def is_han(character):
    codepoint = ord(character)
    index = bisect_right(HAN_STARTS, codepoint) - 1
    return index >= 0 and codepoint <= HAN_RANGES[index][1]


def allowed_text(text):
    return all(not is_han(ch) and ch != "\ufffd" and (ord(ch) >= 32 or ch in "\n\t\r")
               and not 0x7F <= ord(ch) <= 0x9F and not 0xD800 <= ord(ch) <= 0xDFFF
               for ch in text)
