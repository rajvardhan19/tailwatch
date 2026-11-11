from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

from tailwatch.product.delivery import FileSender, StubSender, _transmit
from tailwatch.product.messages import LinkSigner, render_alert, render_verification

SIGNER = LinkSigner("unit-test-link-secret-0123456789abcdef", "https://tailwatch.example.invalid/")
DELIVERY = UUID(int=7)


def test_link_tokens_are_deterministic_purpose_bound_and_kept_out_of_server_logs():
    token = SIGNER.token("unsubscribe", DELIVERY)
    assert token == SIGNER.token("unsubscribe", DELIVERY) and len(token) == 43
    assert SIGNER.check("unsubscribe", DELIVERY, token)
    assert not SIGNER.check("verify", DELIVERY, token)
    assert not SIGNER.check("unsubscribe", UUID(int=8), token)
    other = LinkSigner("another-link-secret-0123456789abcdef", "https://tailwatch.example.invalid")
    assert not other.check("unsubscribe", DELIVERY, token)
    link = urlsplit(SIGNER.link("verify", DELIVERY))
    assert link.path == "/confirm" and not link.query
    assert parse_qs(link.fragment)["token"] == [SIGNER.token("verify", DELIVERY)]


@pytest.mark.parametrize(
    ("secret", "url"),
    [
        ("short", "https://tailwatch.example.invalid"),
        ("s" * 32, "http://tailwatch.example.invalid"),
        ("s" * 32, "https://user:pass@tailwatch.example.invalid"),
        ("s" * 32, "https://tailwatch.example.invalid/?next=x"),
        ("s" * 32, "tailwatch.example.invalid"),
    ],
)
def test_link_signer_rejects_weak_or_unsafe_configuration(secret, url):
    with pytest.raises(ValueError):
        LinkSigner(secret, url)


def test_rendering_is_byte_stable_inert_and_never_publishes_synthetic_probabilities():
    public = {
        "visitor": "Fixture A\r\nBcc: someone@example.invalid",
        "host": "Fixture B",
        "calibrated_probability": 0.42,
        "contributions": {"flight": 0.25},
        "evidence": ["synthetic:visit:1"],
    }
    first = render_alert(
        "r@example.invalid", public, synthetic=True, delivery_id=DELIVERY, signer=SIGNER
    )
    again = render_alert(
        "r@example.invalid", public, synthetic=True, delivery_id=DELIVERY, signer=SIGNER
    )
    assert first == again and first.digest() == again.digest()
    assert "\nBcc:" not in first.body and "42.0%" not in first.body
    assert "Calibrated probability: unavailable" in first.body
    assert first.body.startswith("SYNTHETIC TEST DATA") and "Unsubscribe: https://" in first.body
    real = render_alert(
        "r@example.invalid", public, synthetic=False, delivery_id=DELIVERY, signer=SIGNER
    )
    assert "42.0%" in real.body and real.digest() != first.digest()
    verification = render_verification("r@example.invalid", DELIVERY, SIGNER)
    assert SIGNER.token("verify", DELIVERY) in verification.body


def test_quota_refusal_defers_without_contacting_the_provider():
    class MustNotSend(StubSender):
        def send(self, **kwargs):
            raise AssertionError("provider contacted after refused reservation")

    def refuse() -> None:
        raise ValueError("durable budget exhausted")

    message = render_verification("r@example.invalid", DELIVERY, SIGNER)
    assert _transmit(MustNotSend(), message, DELIVERY, refuse)[0] == "deferred"


def test_file_mailbox_is_idempotent_only_for_identical_payloads(tmp_path):
    sender = FileSender(tmp_path)
    kwargs = {"destination": "r@example.invalid", "subject": "s", "body": "b"}
    key = str(DELIVERY)
    assert sender.send(**kwargs, idempotency_key=key) == sender.send(**kwargs, idempotency_key=key)
    with pytest.raises(ValueError, match="different payload"):
        sender.send(**(kwargs | {"body": "changed"}), idempotency_key=key)
    with pytest.raises(ValueError):
        sender.send(**kwargs, idempotency_key="../escape")
    assert [p.name for p in tmp_path.iterdir()] == [f"{key}.txt"]
