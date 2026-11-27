#!/usr/bin/env python3
"""Oracle for watch-the-vm: restore the damaged binary, then recover the input.

The shipped /app/1983.exe is deliberately broken in two ways:
  * every section header's VirtualAddress / PointerToRawData is zeroed, so no
    loader or disassembler can map it;
  * the .text code section is XOR-encrypted with a repeating key, so even once
    the headers are rebuilt the code is garbage until it is decrypted.

restore() undoes both: it recomputes the section addresses from the surviving
sizes + PE alignment, and XOR-decrypts .text. The result is a working PE.

Once restored, 1983.exe is a stripped PE32+ x86-64 console crackme whose main
runs a control-flow-flattened, mutating-register VM that computes the expected
113-byte input one byte at a time into a stack buffer. The accepted string is
never stored contiguously, so it must be executed. We load the restored image
into Unicorn, replicate main's prologue, run the VM dispatcher, and hook writes
into the expected-input buffer.

Reads /app/1983.exe, writes the recovered string to /app/flag.txt.
"""
import argparse
import struct
import sys
from pathlib import Path

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_PROT_ALL, UC_HOOK_MEM_WRITE
    from unicorn.x86_const import (
        UC_X86_REG_RSP, UC_X86_REG_R11, UC_X86_REG_R12,
        UC_X86_REG_R13, UC_X86_REG_R14, UC_X86_REG_R15,
        UC_X86_REG_RDX, UC_X86_REG_RCX,
    )
except ImportError:
    print("requires unicorn  --  pip install unicorn", file=sys.stderr)
    sys.exit(1)

IMG_BASE = 0x140000000
STACK_BASE = 0x10000000
STACK_SIZE = 0x100000

VM_ENTRY = 0x140002ac0     # right after the CRT init call in main
PROMPT_RIP = 0x140002b80   # `leaq "flag> ", %rcx` -> the VM has finished
EXPECTED_OFFSET = 0xbf     # the 113-byte expected-input buffer lives at rsp+0xbf
EXPECTED_LEN = 113

# Repeating-XOR key the .text code was encrypted with.
TEXT_KEY = b"R3V3RS3M3_1983!!"

FLAG_OUT = Path("/app/flag.txt")


def _roundup(x, a):
    return (x + a - 1) // a * a


def restore(data):
    """Rebuild the wiped section table and XOR-decrypt .text, returning bytes.

    VirtualAddress is recomputed by laying sections out sequentially on
    SectionAlignment; PointerToRawData by laying raw data out on FileAlignment
    from SizeOfHeaders (skipping sections with no raw data). Then the .text
    body [rawptr : rawptr+VirtualSize] is XOR-decrypted with TEXT_KEY.
    """
    data = bytearray(data)
    e_lfanew = struct.unpack_from("<I", data, 0x3c)[0]
    coff = e_lfanew + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    opt = coff + 20
    sect_align = struct.unpack_from("<I", data, opt + 32)[0]
    file_align = struct.unpack_from("<I", data, opt + 36)[0]
    size_hdrs = struct.unpack_from("<I", data, opt + 60)[0]
    sec_off = opt + struct.unpack_from("<H", data, coff + 16)[0]

    va = sect_align
    raw_cur = size_hdrs
    text_ro = text_vs = None
    for i in range(nsec):
        b = sec_off + i * 40
        name = data[b:b + 8].rstrip(b"\0")
        vsize = struct.unpack_from("<I", data, b + 8)[0]
        rsize = struct.unpack_from("<I", data, b + 16)[0]
        struct.pack_into("<I", data, b + 12, va)
        struct.pack_into("<I", data, b + 20, raw_cur if rsize else 0)
        if name == b".text":
            text_ro, text_vs = raw_cur, vsize
        va = _roundup(va + vsize, sect_align)
        if rsize:
            raw_cur = _roundup(raw_cur + rsize, file_align)

    for i in range(text_vs):
        data[text_ro + i] ^= TEXT_KEY[i % len(TEXT_KEY)]
    return bytes(data)


def parse_pe_sections(data):
    """Yield (name, vaddr, file_off, raw_size) for each PE section header."""
    e_lfanew = struct.unpack_from("<I", data, 0x3c)[0]
    assert data[e_lfanew:e_lfanew + 4] == b"PE\0\0"
    coff = e_lfanew + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    opthdr_size = struct.unpack_from("<H", data, coff + 16)[0]
    sec_off = coff + 20 + opthdr_size
    for i in range(nsec):
        h = data[sec_off + i * 40:sec_off + (i + 1) * 40]
        name = h[:8].rstrip(b"\0").decode("latin-1")
        _vsize, vaddr, raw_size, raw_off = struct.unpack_from("<IIII", h, 8)
        yield name, vaddr, raw_off, raw_size


def main():
    ap = argparse.ArgumentParser(description="recover the input 1983.exe accepts")
    ap.add_argument("binary", nargs="?", default="/app/1983.exe", type=Path)
    args = ap.parse_args()

    # Restore the deliberately-broken binary (rebuild headers + decrypt .text)
    # before doing anything else -- nothing maps or runs until this is done.
    data = restore(args.binary.read_bytes())
    sys.stderr.write("[+] restored section table + decrypted .text\n")

    mu = Uc(UC_ARCH_X86, UC_MODE_64)

    # Map the PE image as one RWX region, then drop each raw section at its vaddr.
    mu.mem_map(IMG_BASE, 0x20000, UC_PROT_ALL)
    for name, vaddr, raw_off, raw_size in parse_pe_sections(data):
        if raw_size == 0:
            continue
        mu.mem_write(IMG_BASE + vaddr, data[raw_off:raw_off + raw_size])
        sys.stderr.write(f"[+] loaded {name:8s} at {IMG_BASE + vaddr:#x} size {raw_size:#x}\n")

    # Stack, with a fake return address so a stray `ret` doesn't fault.
    mu.mem_map(STACK_BASE, STACK_SIZE, UC_PROT_ALL)
    rsp = STACK_BASE + STACK_SIZE - 0x1000
    mu.mem_write(rsp, struct.pack("<Q", 0xdead0000))

    # Replicate main's prologue so the VM dispatcher runs under real conditions:
    #   zero [rsp+0xb8 .. +0x130); seed [rsp+0xb0]/[rsp+0xa8] from .rdata
    #   constants; r12=0 r11=1 rdx=4 rcx=0x13579bd; r13=frame top,
    #   r15=jump-table base, r14=instruction tape.
    mu.mem_write(rsp + 0xb8, b"\0" * 0x78)
    mu.mem_write(rsp + 0xb0, bytes(mu.mem_read(0x140005770, 8)))
    mu.mem_write(rsp + 0xa8, bytes(mu.mem_read(0x140005778, 8)))
    mu.reg_write(UC_X86_REG_RSP, rsp)
    mu.reg_write(UC_X86_REG_R12, 0)
    mu.reg_write(UC_X86_REG_R11, 1)
    mu.reg_write(UC_X86_REG_RDX, 4)
    mu.reg_write(UC_X86_REG_RCX, 0x13579bd)
    mu.reg_write(UC_X86_REG_R13, rsp + 0x130)
    mu.reg_write(UC_X86_REG_R14, 0x140005580)
    mu.reg_write(UC_X86_REG_R15, 0x140005018)

    # Capture every byte written into the expected-input buffer.
    buf_start = rsp + EXPECTED_OFFSET
    buf_end = buf_start + EXPECTED_LEN
    captured = [None] * EXPECTED_LEN

    def write_hook(uc, access, addr, size, value, user_data):
        for off in range(size):
            a = addr + off
            if buf_start <= a < buf_end:
                captured[a - buf_start] = (value >> (8 * off)) & 0xff
    mu.hook_add(UC_HOOK_MEM_WRITE, write_hook)

    sys.stderr.write(f"[+] emulating {VM_ENTRY:#x} -> {PROMPT_RIP:#x}\n")
    try:
        mu.emu_start(VM_ENTRY, PROMPT_RIP, timeout=10_000_000, count=2_000_000)
    except Exception as e:  # emulation may stop early; the buffer is what matters
        sys.stderr.write(f"[!] emulation stopped: {e}\n")

    # Prefer bytes the hook saw land; fall back to whatever is in the buffer.
    raw = bytes(mu.mem_read(buf_start, EXPECTED_LEN))
    out = bytes((captured[i] if captured[i] is not None else raw[i])
                for i in range(EXPECTED_LEN))

    FLAG_OUT.write_bytes(out)
    sys.stderr.write(f"[+] wrote {len(out)} bytes to {FLAG_OUT}: {out.decode('latin-1')!r}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
