#!/usr/bin/env python3
"""Sanity checks for the redaction rules and sensitive-file filter.
Run: python tests/smoke_test.py  (stdlib only, no pytest needed)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from review import filter_sensitive_files, redact  # noqa: E402


def test_redact():
    flat_samples = {
        "sk-abcdefghijklmnopqrstuvwxyz123456":  "[REDACTED:openai-key]",
        "ghp_" + "a" * 36:                       "[REDACTED:github-token]",
        "xoxb-1234-abcd":                        "[REDACTED:slack-token]",
        "AKIAABCDEFGHIJKLMNOP":                  "[REDACTED:aws-key]",
        "AIza" + "a" * 35:                       "[REDACTED:google-api-key]",
    }
    for raw, tag in flat_samples.items():
        out, found = redact(f"+const k = {raw};")
        assert tag in out and raw not in out, f"FAILED on {tag}: {out}"
        assert found, f"no redaction reported for {tag}"

    jwt = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
           "dBjftJeZ4CVPmB92K27uhbUJU1p1rwW1gFWFOEjXk")
    out, _ = redact(f"+auth_header = {jwt}")
    assert "[REDACTED:jwt]" in out and jwt not in out, out

    out, _ = redact('+password = "hunter22"')
    assert "hunter22" not in out and "[REDACTED]" in out, out
    out, _ = redact('+  "api_key": "abcd1234",')
    assert "abcd1234" not in out, out

    pem = ("-----BEGIN RSA PRIVATE KEY-----\n"
           "+MIIEpAIBAAKCAQEA7examplekeymaterial\n"
           "-----END RSA PRIVATE KEY-----")
    out, _ = redact(pem)
    assert "MIIEpAIBAAKCAQEA" not in out, out

    out, _ = redact("+Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789")
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in out, out

    # Clean code must pass through untouched
    clean = "+def add(a, b):\n+    return a + b\n"
    out, found = redact(clean)
    assert out == clean and not found, (out, found)

    print("redact:           OK")


def test_filter():
    diff = (
        "diff --git a/src/app.py b/src/app.py\n+print('hi')\n"
        "diff --git a/.env b/.env\n+SECRET=topsecret\n"
        "diff --git a/certs/server.pem b/certs/server.pem\n+pemdata\n"
        "diff --git a/docs/readme.md b/docs/readme.md\n+hello\n"
    )
    kept, removed = filter_sensitive_files(diff)
    assert ".env" in removed and "certs/server.pem" in removed, removed
    assert "src/app.py" not in removed and "docs/readme.md" not in removed, removed
    assert "topsecret" not in kept and "pemdata" not in kept, kept
    assert "print('hi')" in kept and "+hello" in kept, kept
    print("file filter:      OK")


if __name__ == "__main__":
    test_redact()
    test_filter()
    print("smoke test: all assertions passed")
