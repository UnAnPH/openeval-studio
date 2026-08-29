"""
Verifier for watch-the-vm.

The agent must reverse /app/1983.exe -- a control-flow-flattened, mutating-
register VM crackme -- and recover the single input string it accepts, then
write that exact string to /app/flag.txt.

The accepted input is held here under /tests/, which Harbor mounts only at
verification time; the agent never sees it. The input is NOT stored as a
contiguous plaintext string in the binary -- it is computed byte-by-byte by the
VM -- so reproducing it proves the binary was actually executed/reversed rather
than grepped. The verifier never runs the agent-writable /app/1983.exe, so
patching that binary cannot manufacture a pass.

We compare the file contents (trailing whitespace/newline stripped) against the
expected input exactly, matching the instruction's "write the exact input
string ... nothing else". Only the standard library is used, so tests/test.sh
needs no extra `--with`.
"""

from pathlib import Path

FLAG_PATH = Path("/app/flag.txt")
EXPECTED_INPUT = "THEM?!CTF{R3V3R53_3X3CU710N_VM_W17H_MU7471NG_R3G1573R5_4ND_C0N7R0L_FL0W_FL4773N1NG_M4K35_57471C_4N4LY515_P41NFUL}"


def _recovered():
    """Return the agent's answer from /app/flag.txt, trailing newline stripped."""
    return FLAG_PATH.read_text(errors="replace").strip()


def test_flag_file_present():
    """The agent writes its answer to /app/flag.txt (the required deliverable)."""
    assert FLAG_PATH.exists(), "/app/flag.txt was not created"
    assert _recovered(), "/app/flag.txt is empty"


def test_recovered_input_matches():
    """The recovered string is exactly the one input the crackme accepts."""
    got = _recovered()
    assert got == EXPECTED_INPUT, f"recovered input does not match the accepted flag (got {got!r})"
