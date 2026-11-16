"""Regressions for split Korean characters, forbidden Han and partial EOS."""

import struct
import unittest

from utf8_policy_table import INVALID, compile_table, transition, utf8_states


class ByteTokenizer:
    tokens = tuple(bytes([value]) for value in range(256)) + (b"<EOS>",)
    token_types = (1,) * 256 + (3,)

    def token_bytes(self, token):
        return self.tokens[token]


class TestUtf8PolicyTable(unittest.TestCase):
    def test_split_korean_and_forbidden_scalar(self):
        rows = utf8_states()
        split = "아홉 시".encode("utf-8")
        state = transition(0, split[:5], rows)
        self.assertNotIn(state, (0, INVALID))
        self.assertEqual(transition(state, split[5:], rows), 0)
        self.assertEqual(transition(0, split[5:], rows), INVALID)
        for character in ("漢", "㐀", "𠀀", "金", "�"):
            self.assertEqual(transition(0, character.encode("utf-8"), rows), INVALID)
        for broken in (b"\xc0\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80"):
            self.assertEqual(transition(0, broken, rows), INVALID)

    def test_eos_and_token_budget_distances(self):
        payload, manifest = compile_table(ByteTokenizer(), {256})
        _, vocab, states, stops = struct.unpack_from("<8sIII", payload)
        offset = 20 + 4 * stops
        distance = manifest["completion_tokens_by_state"]
        self.assertEqual(payload[offset + 256], 0)
        for state in range(1, states):
            self.assertEqual(payload[offset + state * vocab + 256], INVALID)
        state = payload[offset + 0xF0]
        self.assertEqual(distance[state], 3)
        for token, remaining in ((0x9F, 2), (0x98, 1), (0x80, 0)):
            state = payload[offset + state * vocab + token]
            self.assertEqual(distance[state], remaining)
        self.assertEqual(state, 0)


if __name__ == "__main__":
    unittest.main()
