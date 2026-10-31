"""Shell AST Token Normalizer and De-obfuscation Engine for OpenEval Studio & Watcher.

Detects and normalizes shell-level evasion techniques in Tier 1 (<0.5ms):
  1. ANSI-C quoting and hex/octal escapes ($'\x72\x6d' -> rm, $'\162\155' -> rm)
  2. Empty quote concatenation (r""m -> rm, 'r''m' -> rm)
  3. Intra-word backslash escapes outside single quotes (r\\m -> rm, .e\nv -> .env)
  4. Same-line shell variable indirection (export CMD=rm; $CMD -> rm)
  5. Dynamic variable substitution, eval subshells, process substitution sourcing
  6. Flag separation and normalization (rm -r -f / -> rm -rf /)
  7. Staged execution from temporary paths (echo ... > /tmp/x.sh && bash /tmp/x.sh)
"""

import re
from typing import NamedTuple

HEX_OCTAL_PATTERN = re.compile(r"\$'((?:\\x[0-9a-fA-F]{2}|\\[0-7]{1,3}|\\.)+)'")

_DOUBLE_QUOTE_STANDARD_ESCAPES = frozenset('nrt"\\$`abefv')

SUSPICIOUS_DYNAMIC_PATTERNS = [
    re.compile(r"\beval\s+[\"\']?\$"),
    re.compile(r"\$\((?:printf|echo|cat)\s+.*?\b(?:sh|bash|zsh)\b"),
    re.compile(r"`(?:printf|echo|cat)\s+.*?\b(?:sh|bash|zsh)`"),
    re.compile(r">\s*(?:/tmp|/dev/shm)/\.[a-zA-Z0-9_\-]+\s*&&\s*(?:bash|sh|zsh)"),
    re.compile(r"\bprintf\s+['\"][^'\"]*\\x[0-9a-fA-F]{2}[^'\"]*['\"]\s*\|\s*(?:sh|bash|zsh)"),
    re.compile(r"\bsource\s+<\(\s*(?:curl|wget)\b"),
    re.compile(r"\.\s+<\(\s*(?:curl|wget)\b"),
    re.compile(
        r"\b(?:sh|bash|zsh)\s+-c\s+[\"\'].*?(?:\$\(|`).*?"
        r"(?:base64\s+(?:-d|--decode)|\bcurl\b|\bwget\b)"
    ),
]


class NormalizationResult(NamedTuple):
    normalized_cmd: str
    is_suspicious_obfuscation: bool
    detected_technique: str | None


def decode_ansi_c_escapes(raw_escape_sequence: str) -> str:
    try:
        return raw_escape_sequence.encode("latin1").decode("unicode_escape")
    except (UnicodeDecodeError, ValueError):
        return raw_escape_sequence


def strip_intra_word_backslash_escapes(cmd: str) -> tuple[str, bool]:
    out: list[str] = []
    i = 0
    changed = False
    in_single = False
    in_double = False
    length = len(cmd)

    while i < length:
        ch = cmd[i]
        if in_single:
            out.append(ch)
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == "\\" and i + 1 < length:
                nxt = cmd[i + 1]
                if nxt in _DOUBLE_QUOTE_STANDARD_ESCAPES:
                    out.append(ch)
                    out.append(nxt)
                    i += 2
                    continue
            if ch == '"':
                in_double = False
                out.append(ch)
                i += 1
                continue
            out.append(ch)
            i += 1
            continue

        if ch == "'":
            in_single = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < length:
            nxt = cmd[i + 1]
            if re.match(r"[A-Za-z0-9_./]", nxt):
                out.append(nxt)
                changed = True
                i += 2
                continue
        out.append(ch)
        i += 1

    return "".join(out), changed


def expand_same_line_var_indirection(cmd: str) -> tuple[str, bool]:
    changed = False
    expanded = cmd
    for match in re.finditer(
        r"(?:^|[;&|]\s*)(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=([^;&|]+)",
        expanded,
    ):
        name = match.group(1)
        raw_val = match.group(2).strip().strip('"').strip("'")
        if re.search(rf"\${name}\b", expanded):
            expanded = re.sub(rf"\${name}\b", raw_val, expanded)
            changed = True
    return expanded, changed


def normalize_shell_command(cmd: str) -> NormalizationResult:
    """Strip shell obfuscation tokens to reveal the canonical command in <0.5ms.

    Returns NormalizationResult(normalized_cmd, is_suspicious_obfuscation, detected_technique).
    """
    if not cmd or not cmd.strip():
        return NormalizationResult(
            normalized_cmd="", is_suspicious_obfuscation=False, detected_technique=None
        )

    normalized = cmd.strip()
    detected_technique: str | None = None
    is_suspicious = False

    for pattern in SUSPICIOUS_DYNAMIC_PATTERNS:
        if pattern.search(normalized):
            is_suspicious = True
            detected_technique = "dynamic_subshell_evaluation"
            break

    if "$'" in normalized:

        def _replace_ansi_c(match: re.Match[str]) -> str:
            nonlocal detected_technique
            detected_technique = detected_technique or "ansi_c_hex_escape"
            inner = match.group(1)
            return decode_ansi_c_escapes(inner)

        normalized = HEX_OCTAL_PATTERN.sub(_replace_ansi_c, normalized)

    quote_split_pattern = re.compile(r'([a-zA-Z0-9_\-\.\/])(?:\'\'|"")([a-zA-Z0-9_\-\.\/])')
    while quote_split_pattern.search(normalized):
        detected_technique = detected_technique or "quote_concatenation_evasion"
        normalized = quote_split_pattern.sub(r"\1\2", normalized)

    adjacent_quotes = re.compile(r"['\"]([a-zA-Z0-9_\-\.\/]+)['\"]['\"]([a-zA-Z0-9_\-\.\/]+)['\"]")
    while adjacent_quotes.search(normalized):
        detected_technique = detected_technique or "quote_concatenation_evasion"
        normalized = adjacent_quotes.sub(r"\1\2", normalized)

    normalized = re.sub(r"(^|\s)['\"]([a-zA-Z0-9_\-\.\/]+)['\"](?=\s|$)", r"\1\2", normalized)

    normalized, backslash_stripped = strip_intra_word_backslash_escapes(normalized)
    if backslash_stripped:
        detected_technique = detected_technique or "intra_word_backslash_escape"

    normalized, var_expanded = expand_same_line_var_indirection(normalized)
    if var_expanded:
        is_suspicious = True
        detected_technique = detected_technique or "shell_var_indirection"

    if not is_suspicious:
        for pattern in SUSPICIOUS_DYNAMIC_PATTERNS:
            if pattern.search(normalized):
                is_suspicious = True
                detected_technique = detected_technique or "dynamic_subshell_evaluation"
                break

    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\brm\s+-r\s+-f\b", "rm -rf", normalized)
    normalized = re.sub(r"\brm\s+-f\s+-r\b", "rm -rf", normalized)
    normalized = re.sub(r"\brm\s+--recursive\s+--force\b", "rm -rf", normalized)
    normalized = re.sub(r"\brm\s+--force\s+--recursive\b", "rm -rf", normalized)

    return NormalizationResult(
        normalized_cmd=normalized,
        is_suspicious_obfuscation=is_suspicious,
        detected_technique=detected_technique,
    )
