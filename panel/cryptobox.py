"""Szyfrowanie eksportu konfiguracji. Tylko biblioteka standardowa.

Konstrukcja: scrypt -> dwa klucze, ChaCha20 (RFC 8439) do szyfrowania,
HMAC-SHA256 do uwierzytelnienia szyfrogramu (encrypt-then-MAC).

Kopia zawiera PSK wszystkich peerow. Bez hasla laduje na dysku jawnym
tekstem - a skoro ma trafic na udzial sieciowy, warto ja zaszyfrowac.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import struct

MAGIC = b"WGPB1"
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1

_CONSTANTS = b"expand 32-byte k"


def _rotl(value: int, count: int) -> int:
    value &= 0xFFFFFFFF
    return ((value << count) | (value >> (32 - count))) & 0xFFFFFFFF


def _quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotl(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotl(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotl(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotl(state[b] ^ state[c], 7)


def chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    if len(key) != 32 or len(nonce) != 12:
        raise ValueError("ChaCha20: klucz 32 B, nonce 12 B")
    state = list(struct.unpack("<4I", _CONSTANTS))
    state += list(struct.unpack("<8I", key))
    state.append(counter & 0xFFFFFFFF)
    state += list(struct.unpack("<3I", nonce))

    working = list(state)
    for _ in range(10):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)
    out = [(working[i] + state[i]) & 0xFFFFFFFF for i in range(16)]
    return struct.pack("<16I", *out)


def chacha20(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    for offset in range(0, len(data), 64):
        block = chacha20_block(key, counter + offset // 64, nonce)
        chunk = data[offset:offset + 64]
        for index, byte in enumerate(chunk):
            out[offset + index] = byte ^ block[index]
    return bytes(out)


def _keys(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    material = hashlib.scrypt(
        passphrase.encode("utf8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=64, maxmem=64 * 1024 * 1024,
    )
    return material[:32], material[32:]


def encrypt(plaintext: bytes, passphrase: str) -> bytes:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    enc_key, mac_key = _keys(passphrase, salt)
    ciphertext = chacha20(enc_key, 1, nonce, plaintext)
    header = MAGIC + salt + nonce
    tag = hmac.new(mac_key, header + ciphertext, hashlib.sha256).digest()
    return header + ciphertext + tag


def decrypt(blob: bytes, passphrase: str) -> bytes:
    if len(blob) < len(MAGIC) + 16 + 12 + 32 or not blob.startswith(MAGIC):
        raise ValueError("to nie jest zaszyfrowana kopia wg-panel")
    salt = blob[5:21]
    nonce = blob[21:33]
    ciphertext = blob[33:-32]
    tag = blob[-32:]
    enc_key, mac_key = _keys(passphrase, salt)
    expected = hmac.new(mac_key, blob[:-32], hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise ValueError("zle haslo albo uszkodzony plik")
    return chacha20(enc_key, 1, nonce, ciphertext)


def is_encrypted(blob: bytes) -> bool:
    return blob.startswith(MAGIC)
