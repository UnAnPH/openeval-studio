"""
The verifier. Replace the example tests below with assertions that check
whether the agent solved your task. Full guidance: docs/task-anatomy.md →
tests/test_outputs.py section.

Each test:
  - needs a docstring (it surfaces in the rubric review)
  - should check behavior (file contents, runtime state), not source patterns
  - should trace to a named requirement in instruction.md
  - must survive an adversarial agent (agents run as root in /app/)

Non-stdlib imports: declare each one with `--with <pkg>` in tests/test.sh.
uvx runs pytest in an isolated venv that does not see container site-packages,
so a `ModuleNotFoundError` at pytest collection time means a missing `--with`
declaration, not a bug in your tests.
"""

from pathlib import Path
import random
import struct
import sys
import os
import zlib
import string
import subprocess

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


def crc32(data: bytes) -> int:
    """Standard CRC-32 (zlib/ZIP)."""
    return zlib.crc32(data) & 0xFFFFFFFF

def digest_hashA(data: bytes) -> bytes:
    """Per-entry digest. Two interacting accumulators + final avalanche.
    Distinct algorithm from master_check: dual-state, multiply-xor mix.
    """
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
    """Checksum over the CD bytes. Single accumulator, but folds in a
    position-weighted running sum — structurally unlike digest_hashA.
    """
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

MAGIC = 0xA39CB251
def pack(entries) -> bytes:
    """Build a valid archive from [(name: bytes, data: bytes), ...].

    Entries are emitted in the order given; callers pass them already sorted
    so the encoding is canonical (one correct byte layout per file set).
    """
    # Use any non-standard magic to use as a proprietary marker instead of "AKRV"
    out = bytearray(struct.pack("<I", MAGIC))
    local_offsets = []
    for name, data in entries:
        local_offsets.append(len(out))
        out += b"LR"
        out += struct.pack("<H", len(name))
        out += struct.pack("<I", len(data))
        out += struct.pack("<I", crc32(data))
        out += name
        out += data

    dir_offset = len(out)
    cd = bytearray()
    for (name, data), off in zip(entries, local_offsets):
        cd += b"CD"
        cd += struct.pack("<I", off)
        cd += struct.pack("<H", len(name))
        cd += struct.pack("<I", len(data))
        cd += struct.pack("<I", crc32(data))
        cd += digest_hashA(data)
        cd += struct.pack("<H", rs_chunk(len(data)))
        cd += rs_parity_all(data)
        cd += name
    out += cd

    out += b"ED"
    out += struct.pack("<I", dir_offset)
    out += struct.pack("<I", len(entries))
    out += struct.pack("<I", master_check(bytes(cd)))

    return bytes(out)

def dir_offset_of(blob: bytes) -> int:
    """Read the central-directory offset from a valid archive's footer."""
    FOOTER_SIZE = 14
    if len(blob) < 4 + FOOTER_SIZE or blob[:4] != struct.pack("<I", MAGIC):
        sys.exit("corrupt: input is not a valid .archive (bad header)")
    foot = len(blob) - FOOTER_SIZE
    if blob[foot:foot + 2] != b"ED":
        sys.exit("corrupt: input is not a valid .archive (bad footer)")
    off = struct.unpack_from("<I", blob, foot + 2)[0]
    if off > foot:
        sys.exit("corrupt: input footer is itself corrupt (dir_offset out of range)")
    return off

def corrupt(blob: bytes, mode: str, seed: int = 0) -> bytes:
    """Return a corrupted copy of `blob`, damaging only the trailer.

    head = header + local records (kept intact); trailer = central dir + footer.
    """
    off = dir_offset_of(blob)
    head, trailer = blob[:off], blob[off:]

    if mode == "zeroed":
        return head + b"\x00" * len(trailer)

    if mode == "truncated":
        return head

    if mode == "scrambled":
        rng = random.Random(seed)
        order = list(range(len(trailer)))
        rng.shuffle(order)
        out = bytearray(trailer[i] for i in order)

        if bytes(out) == trailer and len(out) >= 2:
            out[0], out[-1] = out[-1], out[0]
        if len(out) >= 2 and out[0] == ord("L") and out[1] == ord("R"):
            for j in range(2, len(out)):
                if out[j] != ord("L"):
                    out[0], out[j] = out[j], out[0]
                    break
        return head + bytes(out)

    if mode == "data_error":
        rng = random.Random(seed)
        out = bytearray(blob)
        # walk local records inline (no helper dependency)
        dir_off = dir_offset_of(blob)
        p = 4
        while p < dir_off:
            if blob[p:p + 2] != b"LR":
                break
            name_len = struct.unpack_from("<H", blob, p + 2)[0]
            data_len = struct.unpack_from("<I", blob, p + 4)[0]
            data_start = p + 12 + name_len
            if data_len > 0:
                nchunks = (data_len + RS_K - 1) // RS_K
                forced_chunk = rng.randrange(nchunks)        # force >=1 error per file
                for ci, off2 in enumerate(range(0, data_len, RS_K)):
                    clen = min(RS_K, data_len - off2)
                    lo = 1 if ci == forced_chunk else 0      # forced chunk gets >=1
                    nerr = min(rng.randint(lo, 2), clen)
                    if nerr:
                        for pos in rng.sample(range(clen), nerr):
                            idx = data_start + off2 + pos
                            new = out[idx]
                            while new == out[idx]:           # ensure the byte changes
                                new = rng.randrange(256)
                            out[idx] = new
            p = data_start + data_len
        return bytes(out)

def generate_random_entries(seed: int = None, min_files: int = 1, max_files: int = 10, min_size: int = 2048, max_size: int = 4096):
    """Generate a random testcase to be packed with pack()."""

    # Make sure a seed is randomly generated is not provided
    if seed == None:
        seed = int.from_bytes(os.urandom(4), 'big')

    entries = []
    rng = random.Random(seed)
    n = rng.randint(min_files, max_files)

    for i in range(n):
        stem = "".join(rng.choices(string.ascii_lowercase + string.digits,
                                   k=rng.randint(3, 10)))
        ext = rng.choice(["txt", "bin", "dat", "log"])
        name = f"{i:03d}_{stem}.{ext}".encode()

        size = rng.randint(min_size, max_size)
        if ext in ("txt", "log"):
            alphabet = string.ascii_letters + string.digits + " \n"
            data = "".join(rng.choices(alphabet, k=size)).encode("utf-8")
        else:
            data = rng.randbytes(size)
        entries.append((name, data))
    return entries

def run_repair(path, corrupt_archive):
    in_path = path / 'input.archive'
    in_path.write_bytes(corrupt_archive)
    out_dir = path / 'out'
    out_dir.mkdir()

    result = subprocess.run(
        ['/app/repair', str(in_path), str(out_dir)],
        capture_output = True,
        timeout = 60
    )

    return result, out_dir

def check_repair(path, archive, corrupt_archive):
    assert corrupt_archive != archive, "corrupt() produced no change"
    assert os.path.exists('/app/repair'), 'Repair tool does not exist'

    result, out_dir = run_repair(path, corrupt_archive)
    assert result.returncode == 0, f"repair exited {result.returncode}: {result.stderr.decode(errors='replace')}"

    recovered = out_dir / 'recovered-archive.archive'
    assert os.path.exists(recovered), 'repair did not write recovered-archive.archive'
    assert recovered.read_bytes() == archive, 'recovered file is not the same as original'

    # the recovered archive must also be accepted by the reference extractor
    assert os.path.exists('/app/extract'), 'extractor not found'
    ext_dir = path / 'extracted'; ext_dir.mkdir()
    ex = subprocess.run(['/app/extract', str(recovered), str(ext_dir)],
                        capture_output=True, timeout=60)
    assert ex.returncode == 0, \
        f"/app/extract rejected the recovered archive: {ex.stderr.decode(errors='replace')}"

def test_determinism(tmp_path):
    """Repairing the same corrupted input twice yields byte-identical output."""
    for mode in ("data_error", "scrambled", "zeroed", "truncated"):
        archive = pack(generate_random_entries())
        corrupted = corrupt(archive, mode, random.randrange(1 << 100))
        assert corrupted != archive, f"corrupt() produced no change for mode {mode}"
        assert os.path.exists('/app/repair'), f'Repair tool does not exist for mode {mode}'
        a = tmp_path / f"{mode}_a"; a.mkdir()
        b = tmp_path / f"{mode}_b"; b.mkdir()
        r1, out1 = run_repair(a, corrupted)
        r2, out2 = run_repair(b, corrupted)
        assert r1.returncode == 0 and r2.returncode == 0, f"repair failed for mode {mode}"
        first  = (out1 / 'recovered-archive.archive').read_bytes()
        second = (out2 / 'recovered-archive.archive').read_bytes()
        assert first == second, f"repair output is not deterministic for the same input for mode {mode}"
        assert first == archive, f"deterministic but not the byte-identical original for mode {mode}"

def test_scramble(tmp_path):
    """Test repair for scrambled archives"""
    archive = pack(generate_random_entries())

    corruption_seed = 0
    corrupt_archive = corrupt(archive, 'scrambled', corruption_seed)

    while corrupt_archive == archive:
        corruption_seed += 1
        corrupt_archive = corrupt(archive, 'scrambled', corruption_seed)

    check_repair(tmp_path, archive, corrupt_archive)

def test_zeroed(tmp_path):
    """Test the repair for zeroed archives"""
    archive = pack(generate_random_entries())
    corrupted_archive = corrupt(archive, 'zeroed', random.randrange(1 << 100))
    check_repair(tmp_path, archive, corrupted_archive)

def test_truncated(tmp_path):
    """Test the repair for truncated archives"""
    archive = pack(generate_random_entries())
    corrupted_archive = corrupt(archive, 'truncated', random.randrange(1 << 100))
    check_repair(tmp_path, archive, corrupted_archive)

def test_data_errors(tmp_path):
    """Repair recovers the byte-identical original from data corrupted bytes."""
    archive = pack(generate_random_entries())
    corrupted_archive = corrupt(archive, 'data_error', random.randrange(1 << 100))
    check_repair(tmp_path, archive, corrupted_archive)
