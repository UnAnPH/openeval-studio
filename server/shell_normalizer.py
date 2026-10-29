"""Shell AST Token Normalizer and De-obfuscation Engine for OpenEval Studio & Watcher.

Detects and normalizes shell-level evasion techniques in Tier 1 (<0.5ms):
  1. ANSI-C quoting and hex/octal escapes ($'\x72\x6d' -> rm, $'\162\155' -> rm)
  2. Empty quote concatenation (r""m -> rm, 'r''m' -> rm)
  3. Dynamic variable substitution and eval subshells
  4. Flag separation and normalization (rm -r -f / -> rm -rf /)
  5. Staged execution from temporary paths (echo ... > /tmp/x.sh && bash /tmp/x.sh)
"""

import re
from typing import NamedTuple

# Regex to match ANSI-C quoting with hex or octal escapes: $'\x63\x75\x72\x6c' or $'\143\165\162\154'
HEX_OCTAL_PATTERN = re.compile(r"\$'((?:\\x[0-9a-fA-F]{2}|\\[0-7]{1,3}|\\.)+)'")

# Regex to detect suspicious subshell evaluation or dynamic execution primitives
SUSPICIOUS_DYNAMIC_PATTERNS = [
    re.compile(r"\beval\s+[\"\']?\$"),                    # eval $VAR or eval $(...)
    re.compile(r"\$\((?:printf|echo|cat)\s+.*?\b(?:sh|bash|zsh)\b"),  # $(printf ...) | sh
    re.compile(r"`(?:printf|echo|cat)\s+.*?\b(?:sh|bash|zsh)\b`"),    # `printf ...` | sh
    re.compile(r">\s*(?:/tmp|/dev/shm)/\.[a-zA-Z0-9_\-]+\s*&&\s*(?:bash|sh|zsh)"), # staged /tmp execution
    re.compile(r"\bprintf\s+['\"][^'\"]*\\x[0-9a-fA-F]{2}[^'\"]*['\"]\s*\|\s*(?:sh|bash|zsh)"), # printf '\x...' | sh
]


class NormalizationResult(NamedTuple):
    normalized_cmd: str
    is_suspicious_obfuscation: bool
    detected_technique: str | None


def decode_ansi_c_escapes(raw_escape_sequence: str) -> str:
    """Decode hex and octal escape sequences safely without invoking a shell."""
    try:
        # Use latin1 to preserve byte values 0x00-0xFF, then decode unicode escapes
        return raw_escape_sequence.encode("latin1").decode("unicode_escape")
    except (UnicodeDecodeError, ValueError):
        return raw_escape_sequence


def normalize_shell_command(cmd: str) -> NormalizationResult:
    """Strip shell obfuscation tokens to reveal the canonical command in <0.5ms.

    Returns NormalizationResult(normalized_cmd, is_suspicious_obfuscation, detected_technique).
    """
    if not cmd or not cmd.strip():
        return NormalizationResult(normalized_cmd="", is_suspicious_obfuscation=False, detected_technique=None)

    normalized = cmd.strip()
    detected_technique: str | None = None
    is_suspicious = False

    # 1. Check for suspicious dynamic subshells / eval constructs
    for pattern in SUSPICIOUS_DYNAMIC_PATTERNS:
        if pattern.search(normalized):
            is_suspicious = True
            detected_technique = "dynamic_subshell_evaluation"
            break

    # 2. Decode ANSI-C quoting: $'\x72\x6d' -> rm
    if "$'" in normalized:
        def _replace_ansi_c(match: re.Match[str]) -> str:
            nonlocal detected_technique
            detected_technique = detected_technique or "ansi_c_hex_escape"
            inner = match.group(1)
            return decode_ansi_c_escapes(inner)

        normalized = HEX_OCTAL_PATTERN.sub(_replace_ansi_c, normalized)

    # 3. Collapse concatenated empty quotes: r""m -> rm, 'r''m' -> rm, r''m -> rm
    quote_split_pattern = re.compile(r'([a-zA-Z0-9_\-\.\/])(?:\'\'|"")([a-zA-Z0-9_\-\.\/])')
    while quote_split_pattern.search(normalized):
        detected_technique = detected_technique or "quote_concatenation_evasion"
        normalized = quote_split_pattern.sub(r"\1\2", normalized)

    # Collapse adjacent single-quoted or double-quoted tokens: 'r''m' -> rm, "r""m" -> rm
    adjacent_quotes = re.compile(r"['\"]([a-zA-Z0-9_\-\.\/]+)['\"]['\"]([a-zA-Z0-9_\-\.\/]+)['\"]")
    while adjacent_quotes.search(normalized):
        detected_technique = detected_technique or "quote_concatenation_evasion"
        normalized = adjacent_quotes.sub(r"\1\2", normalized)

    # Strip unnecessary quotes around single command tokens: 'rm' -> rm, "rm" -> rm
    # Only if token has no spaces inside
    normalized = re.sub(r"(^|\s)['\"]([a-zA-Z0-9_\-\.\/]+)['\"](?=\s|$)", r"\1\2", normalized)

    # 4. Collapse multiple whitespace and normalize flags (rm -r -f / -> rm -rf /)
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
