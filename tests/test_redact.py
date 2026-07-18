from __future__ import annotations

from hypothesis import given, strategies as st

from sai.redact import REDACTED, redact

SECRET = "hunter2secret"


class TestKeyValuePatterns:
    def test_each_configured_name(self):
        for name in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "API_KEY"):
            text = f"export {name}={SECRET}"
            out = redact(text)
            assert SECRET not in out and name in out, name

    def test_name_containing_secret_word(self):
        for name in ("GITHUB_TOKEN", "MY_API_KEY", "DB_PASSWORD", "STRIPE_SECRET_LIVE"):
            out = redact(f"{name}={SECRET}")
            assert SECRET not in out and name in out, name

    def test_aws_star(self):
        out = redact(f"AWS_SECRET_ACCESS_KEY={SECRET} AWS_SESSION_TOKEN={SECRET}")
        assert SECRET not in out and out.count(REDACTED) == 2

    def test_quoted_values(self):
        out = redact(f'PASSWORD="{SECRET} with spaces"')
        assert SECRET not in out and "with spaces" not in out

    def test_single_quoted_values(self):
        out = redact(f"TOKEN='{SECRET}'")
        assert SECRET not in out

    def test_lowercase_names_covered(self):
        assert SECRET not in redact(f"password={SECRET}")

    def test_unbalanced_quote_cannot_bypass(self):
        assert SECRET not in redact(f'PASSWORD="{SECRET}')

    def test_embedded_quote_cannot_leave_a_partial_value(self):
        assert SECRET not in redact(f'TOKEN=xy"{SECRET}')


class TestAuthorizationHeader:
    def test_bearer(self):
        out = redact(f"Authorization: Bearer {SECRET}")
        assert SECRET not in out and "Bearer" not in out
        assert out.startswith("Authorization: ")

    def test_case_insensitive(self):
        assert SECRET not in redact(f"authorization: basic {SECRET}")


class TestSshPrivateKeys:
    BLOCK = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )

    def test_block_redacted(self):
        out = redact(f"before\n{self.BLOCK}\nafter")
        assert "OPENSSH" not in out and "b3BlbnNzaC" not in out
        assert "before" in out and "after" in out

    def test_unterminated_block_redacted_to_end(self):
        out = redact("x\n-----BEGIN RSA PRIVATE KEY-----\nAAAApqrs\ntruncated")
        assert "AAAApqrs" not in out and "truncated" not in out and "x" in out


class TestLongRuns:
    def test_base64_run_over_32_chars(self):
        blob = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0Nw=="  # 40 chars
        assert blob not in redact(f"blob: {blob}")

    def test_hex_run_over_32_chars(self):
        digest = "a" * 20 + "0123456789abcdef"  # 36 hex chars
        assert digest not in redact(f"sha: {digest}")

    def test_run_of_exactly_32_chars_survives(self):
        run = "a1B2" * 8  # 32 chars — spec says "longer than 32"
        assert run in redact(run)


class TestBenignTextSurvives:
    def test_ordinary_error_output(self):
        text = "make: *** No such file or directory. Stop.\ncc -O2 -o lens lens.c"
        assert redact(text) == text

    def test_short_assignments_untouched(self):
        assert redact("CFLAGS=-O2 make lens") == "CFLAGS=-O2 make lens"


# Property test at the redact() layer; the analyst-payload version is invariant 5
# in test_invariants.py.
secret_values = st.text(
    alphabet="ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789",
    min_size=12, max_size=48,
)


@given(secret_values)
def test_property_no_configured_secret_survives(secret):
    for template in (
        f"export AWS_SECRET_ACCESS_KEY={secret}",
        f"DB_PASSWORD={secret}",
        f"curl -H 'Authorization: Bearer {secret}'",
        f"GITHUB_TOKEN={secret} gh pr create",
    ):
        assert secret not in redact(template)
