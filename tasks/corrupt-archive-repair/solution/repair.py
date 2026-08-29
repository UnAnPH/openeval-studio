#!/usr/bin/env python3
"""Reference repair tool for the .archive format (the oracle's solution).

Reconstructs a corrupted archive's trailer (central directory + footer) from its
intact body (header + local records) and writes the byte-identical original to
<output-directory>/recovered-archive.archive.

Usage:
    repair <corrupted-archive.archive> <output-directory>
"""
import os
import struct
import sys
import zlib

MAGIC = 0xA39CB251

GF_POLY = 0x165
RS_K = 239
RS_NPAR = 4
RS_FCR = 0

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256

def gf_init():
    """Initialize the GF tables."""
    x = 1
    for i in range(255):
        _GF_EXP[i] = x
        _GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= GF_POLY
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]

gf_init()

def gf_mul(a, b):
    """Multiply two elements of GF(2^8)."""
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]

def gf_poly_mul(p, q):
    """Multiply two polynomials over GF(2^8)"""
    r = [0] * (len(p) + len(q) - 1)
    for i in range(len(p)):
        for j in range(len(q)):
            r[i + j] ^= gf_mul(p[i], q[j])
    return r

def rn_gen():
    """Generate the rs polynomial."""
    g = [1]
    for i in range(RS_NPAR):
        g = gf_poly_mul(g, [1, _GF_EXP[RS_FCR + i]])
    return g

RS_GEN = rn_gen()
GF_ALPHA = _GF_EXP[1]

def rs_parity(data: bytes) -> bytes:
    """Compute the RS parity (RS_NPAR bytes) for one chunk (len <= RS_K)."""
    out = bytearray(data) + bytearray(RS_NPAR)
    for i in range(len(data)):
        coef = out[i]
        if coef:
            for j in range(1, RS_NPAR + 1):
                out[i + j] ^= gf_mul(RS_GEN[j], coef)
    return bytes(out[len(data):])

def rs_chunk(data_len: int) -> int:
    """Compute the number of RS chunks needed to cover data_len bytes."""
    return (data_len + RS_K - 1) // RS_K

def rs_parity_all(data: bytes) -> bytes:
    """Parity for every RS_K-byte chunk of data, concatenated."""
    out = bytearray()
    for off in range(0, len(data), RS_K):
        out += rs_parity(data[off:off + RS_K])
    return bytes(out)

def _gf_pow(x, p):
      if x == 0:
          return 1 if p == 0 else 0
      return _GF_EXP[(_GF_LOG[x] * p) % 255]

def _gf_inv(x):
      return _GF_EXP[255 - _GF_LOG[x]]

def _poly_eval(msg, x):
      """Evaluate a polynomial (msg[0] = highest-degree coefficient) at x."""
      y = 0
      for c in msg:
          y = gf_mul(y, x) ^ c
      return y

def rs_decode_chunk(data: bytes, parity: bytes) -> bytes:
    """Correct up to RS_NPAR//2 (=2) byte errors in `data` using `parity`.
    Returns the corrected data bytes (same length as `data`)."""
    msg = list(data) + list(parity)
    N = len(msg)
    # syndromes: evaluate the codeword at α^(FCR+i); all zero => no errors
    S = [_poly_eval(msg, _gf_pow(GF_ALPHA, RS_FCR + i)) for i in range(RS_NPAR)]
    if not any(S):
        return bytes(data)
    S0, S1, S2, S3 = S
    det = gf_mul(S1, S1) ^ gf_mul(S0, S2)        # != 0 => two errors, == 0 => one
    if det != 0:
        di = _gf_inv(det)
        lam1 = gf_mul(gf_mul(S2, S1) ^ gf_mul(S0, S3), di)
        lam2 = gf_mul(gf_mul(S1, S3) ^ gf_mul(S2, S2), di)
        # roots of Λ(x)=1+lam1·x+lam2·x²; locators are their inverses
        roots = [x for x in range(1, 256)
                if (1 ^ gf_mul(lam1, x) ^ gf_mul(lam2, gf_mul(x, x))) == 0]
        if len(roots) != 2:
            raise ValueError("RS decode failed (locator roots != 2)")
        X0, X1 = (_gf_inv(r) for r in roots)
        Y0 = gf_mul(S1 ^ gf_mul(S0, X1), _gf_inv(X0 ^ X1))
        pairs = [(X0, Y0), (X1, S0 ^ Y0)]
    else:
        if S0 == 0:
            raise ValueError("RS decode failed (S0 == 0)")
        pairs = [(gf_mul(S1, _gf_inv(S0)), S0)]   # X = S1/S0, magnitude = S0
    out = list(msg)
    for X, Y in pairs:
        k = (N - 1) - _GF_LOG[X]                  # locator α^e maps to byte index N-1-e
        if not (0 <= k < N):
            raise ValueError("RS decode failed (position out of range)")
        out[k] ^= Y
    return bytes(out[:len(data)])

def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def digest_hashA(data: bytes) -> bytes:
    """Per-entry digest in the CD."""
    a = 0xAB9302CF
    b = 0x1B873593
    for byte in data:
        a = (a + byte) & 0xFFFFFFFF
        a = ((a << 7) | (a >> 25)) & 0xFFFFFFFF
        b = (b ^ a) & 0xFFFFFFFF
        b = (b * 0x85EBCA6B) & 0xFFFFFFFF
        a = (a + (b >> 13)) & 0xFFFFFFFF
    a ^= b
    a = (a ^ (a >> 16)) & 0xFFFFFFFF
    a = (a * 0xC2B2AE35) & 0xFFFFFFFF
    a = (a ^ (a >> 15)) & 0xFFFFFFFF
    return struct.pack("<I", a)


def master_check(data: bytes) -> int:
    """Checksum over the CD bytes."""
    h = 0x1357BD13
    acc = 0
    for i, byte in enumerate(data):
        acc = (acc + (byte + 1) * (i + 1)) & 0xFFFFFFFF
        h ^= (byte * 0x9E3779B1) & 0xFFFFFFFF
        h = ((h >> 17) | (h << 15)) & 0xFFFFFFFF
        h = (h + acc + byte) & 0xFFFFFFFF
    h = (h ^ (h << 13)) & 0xFFFFFFFF
    h = (h + acc * 0x2545F491) & 0xFFFFFFFF
    return h & 0xFFFFFFFF

def parse_and_verify(blob):
    """If the trailer survived, return (dir_offset, entries)."""
    n = len(blob)
    if n < 16 or blob[:4] != struct.pack("<I", MAGIC):
        return None

    foot = n - 14
    if blob[foot:foot + 2] != b"ED":
        return None
    dir_offset = struct.unpack_from("<I", blob, foot + 2)[0]
    count      = struct.unpack_from("<I", blob, foot + 6)[0]
    stored     = struct.unpack_from("<I", blob, foot + 10)[0]

    if dir_offset > foot:
        return None
    if master_check(blob[dir_offset:foot]) != stored:
        return None

    entries = []
    q = dir_offset
    for _ in range(count):
        if blob[q:q + 2] != b"CD":
            return None
        b = q + 2
        local_off = struct.unpack_from("<I", blob, b)[0]
        name_len  = struct.unpack_from("<H", blob, b + 4)[0]
        data_len  = struct.unpack_from("<I", blob, b + 6)[0]
        nchunks   = struct.unpack_from("<H", blob, b + 18)[0]
        parity    = blob[b + 20:b + 20 + nchunks * RS_NPAR]
        q = b + 20 + nchunks * RS_NPAR + name_len
        entries.append((local_off, name_len, data_len, parity))

    return dir_offset, entries

def fix_data(blob, parsed):
    """Runs for the data corruption case. RS-decode each record's data against the intact parity."""
    _, entries = parsed
    out = bytearray(blob)
    for local_off, name_len, data_len, parity in entries:
        data_start = local_off + 12 + name_len
        for ci, off in enumerate(range(0, data_len, RS_K)):
            clen = min(RS_K, data_len - off)
            chunk_parity = parity[ci * RS_NPAR:(ci + 1) * RS_NPAR]
            corrected = rs_decode_chunk(bytes(out[data_start + off:data_start + off + clen]), chunk_parity)
            out[data_start + off:data_start + off + clen] = corrected
    return bytes(out)

def repair(blob: bytes) -> bytes:
    """Return the byte-identical original for a trailer-corrupted archive."""
    if len(blob) < 4 or blob[:4] != struct.pack("<I", MAGIC):
        sys.exit("repair: input is not an .archive (bad header)")

    parsed = parse_and_verify(blob)
    if parsed is not None:
        return fix_data(blob, parsed)   # trailer is intact; just fix the data if needed
    # Walk the intact local records; their own length fields delimit them.
    #   LR: "LR" | name_len u16 | data_len u32 | crc32 u32 | name | data
    p, n = 4, len(blob)
    records = []   # (local_offset, name, data_len, crc, data)
    while p + 12 <= n and blob[p:p + 2] == b"LR":
        name_len = struct.unpack_from("<H", blob, p + 2)[0]
        data_len = struct.unpack_from("<I", blob, p + 4)[0]
        crc = struct.unpack_from("<I", blob, p + 8)[0]
        rec_end = p + 12 + name_len + data_len
        if rec_end > n:
            break                      # would run past EOF — not a real record
        name = blob[p + 12:p + 12 + name_len]
        data = blob[p + 12 + name_len:rec_end]
        records.append((p, name, data_len, crc, data))
        p = rec_end
    dir_offset = p

    # Rebuild the central directory by copying the intact per-file fields.
    #   CD: "CD" | local_offset u32 | name_len u16 | data_len u32 | crc32 u32 | name | hashA u32
    cd = bytearray()
    for local_off, name, data_len, crc, data in records:
        cd += b"CD"
        cd += struct.pack("<I", local_off)
        cd += struct.pack("<H", len(name))
        cd += struct.pack("<I", data_len)
        cd += struct.pack("<I", crc)
        cd += digest_hashA(data)
        cd += struct.pack("<H", rs_chunk(data_len))
        cd += rs_parity_all(data)
        cd += name

    # Footer: "ED" | dir_offset u32 | count u32 | master_check u32
    footer = (b"ED"
              + struct.pack("<I", dir_offset)
              + struct.pack("<I", len(records))
              + struct.pack("<I", master_check(bytes(cd))))

    return blob[:dir_offset] + bytes(cd) + footer


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: repair <corrupted-archive.archive> <output-directory>")
    in_path, out_dir = sys.argv[1], sys.argv[2]
    with open(in_path, "rb") as f:
        blob = f.read()
    recovered = repair(blob)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "recovered-archive.archive")
    with open(out_path, "wb") as f:
        f.write(recovered)
    print(f"repair: wrote {out_path} ({len(recovered)} bytes)")


if __name__ == "__main__":
    main()
