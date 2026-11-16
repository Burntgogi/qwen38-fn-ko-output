"""Compile UTF-8 prefix states against the live Qwen byte vocabulary."""

from array import array
from functools import lru_cache
import hashlib
import struct

from output_policy import TABLE_VERSION, allowed_text


INVALID = 255
MAGIC = b"KOUTF8\x01\x00"


@lru_cache(maxsize=1)
def utf8_states():
    """Minimize by the accepting suffixes of every nonempty scalar prefix."""
    endings = {}
    root = bytearray([INVALID]) * 256
    for codepoint in range(0x110000):
        character = chr(codepoint)
        if not allowed_text(character):
            continue
        raw = character.encode("utf-8")
        if len(raw) == 1:
            root[raw[0]] = 0
        else:
            endings[raw[:-1]] = endings.get(raw[:-1], 0) | (1 << raw[-1])

    rows = [root]
    interned = {}
    pending = {}
    for length in (3, 2, 1):
        parents = {}
        for prefix, state in pending.items():
            row = parents.setdefault(prefix[:-1], bytearray([INVALID]) * 256)
            row[prefix[-1]] = state
        for prefix, bitmap in endings.items():
            if len(prefix) != length:
                continue
            row = parents.setdefault(prefix, bytearray([INVALID]) * 256)
            for b in range(0x80, 0xC0):
                if bitmap & (1 << b):
                    row[b] = 0
        pending = {}
        for prefix, row in sorted(parents.items()):
            key = bytes(row)
            if key not in interned:
                if len(rows) >= 63:
                    raise ValueError("the compact UTF-8 table exceeds the state width")
                interned[key] = len(rows)
                rows.append(row)
            pending[prefix] = interned[key]
    for prefix, state in pending.items():
        root[prefix[0]] = state
    return tuple(bytes(row) for row in rows)


def transition(state, piece, rows):
    for byte in piece:
        state = rows[state][byte]
        if state == INVALID:
            break
    return state


def compile_table(tokenizer, stop_ids):
    vocab = len(tokenizer.tokens)
    if vocab != len(tokenizer.token_types):
        raise ValueError("token type count differs from vocabulary size")
    if not stop_ids or any(not 0 <= token < vocab or tokenizer.token_types[token] not in (3, 4)
                           for token in stop_ids):
        raise ValueError("the configured EOS IDs must be control tokens")
    rows = utf8_states()
    pieces = []
    pieces_sha = hashlib.sha256()
    for token, kind in enumerate(tokenizer.token_types):
        piece = tokenizer.token_bytes(token)
        pieces_sha.update(bytes([kind]) + len(piece).to_bytes(4, "little") + piece)
        pieces.append(piece if kind in (1, 6) and piece else None)
    table = bytearray()
    for state in range(len(rows)):
        column = array("B", (transition(state, piece, rows) if piece else INVALID for piece in pieces))
        if state == 0:
            for token in stop_ids:
                column[token] = 0
        table.extend(column)
    # A finite budget must leave enough token slots to finish the code point.
    edges = [set(table[state * vocab:(state + 1) * vocab]) - {INVALID} for state in range(len(rows))]
    distance = [0] + [INVALID] * (len(rows) - 1)
    for budget in range(1, 4):
        for state, targets in enumerate(edges):
            if distance[state] == INVALID and any(distance[target] < budget for target in targets):
                distance[state] = budget
    if INVALID in distance:
        raise ValueError("UTF-8 vocabulary cannot finish a scalar in three continuation tokens")
    payload = struct.pack("<8sIII", MAGIC, vocab, len(rows), len(stop_ids))
    payload += struct.pack(f"<{len(stop_ids)}I", *sorted(stop_ids)) + table
    return payload, {
        "unicode_table": TABLE_VERSION, "vocab_size": vocab, "states": len(rows),
        "table_sha256": hashlib.sha256(payload).hexdigest(), "pieces_sha256": pieces_sha.hexdigest(),
        "stop_ids": sorted(stop_ids), "completion_tokens_by_state": distance,
    }
