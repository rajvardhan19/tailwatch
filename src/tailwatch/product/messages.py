"""Deterministic message rendering and signed recipient links.

Every link token is an HMAC of a purpose and a stable message/delivery ID. The same
inputs therefore render byte-identical messages, which is what makes a provider
idempotent retry safe after an ambiguous timeout. Raw recipient tokens are never
stored or shown to the operator.
"""

import base64
import hashlib
import hmac
import json
import os
import re
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from tailwatch.sources.policy import Contract

Purpose = Literal["verify", "unsubscribe"]


class LinkSigner:
    def __init__(self, secret: str, base_url: str) -> None:
        if len(secret) < 32:
            raise ValueError("link secret must contain at least 32 characters")
        parts = urlsplit(base_url)
        if (
            parts.scheme not in {"https", "http"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("public URL must be an absolute origin/path without credentials")
        if parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("public links require HTTPS outside loopback development")
        self._key = secret.encode()
        self.base_url = base_url.rstrip("/")

    def token(self, purpose: Purpose, identifier: UUID) -> str:
        mac = hmac.new(
            self._key, f"tailwatch-link-v1:{purpose}:{identifier}".encode(), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()

    def check(self, purpose: Purpose, identifier: UUID, token: str) -> bool:
        return hmac.compare_digest(self.token(purpose, identifier), token)

    def link(self, purpose: Purpose, identifier: UUID) -> str:
        # The token travels in the fragment: browsers do not send it to servers or logs.
        fragment = urlencode(
            {"action": purpose, "id": str(identifier), "token": self.token(purpose, identifier)}
        )
        return f"{self.base_url}/confirm#{fragment}"


def signer_from_env() -> LinkSigner | None:
    secret, base = os.environ.get("TAILWATCH_LINK_SECRET"), os.environ.get("TAILWATCH_PUBLIC_URL")
    return LinkSigner(secret, base) if secret and base else None


class Message(Contract):
    destination: str
    subject: str
    body: str

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def _plain(value: object, limit: int = 300) -> str:
    """Untrusted approved content becomes single-line inert text."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_verification(destination: str, message_id: UUID, signer: LinkSigner) -> Message:
    return Message(
        destination=destination,
        subject="Confirm your Tailwatch alert subscription",
        body="\n".join(
            (
                "Someone asked to send reviewed Tailwatch research alerts to this address.",
                "",
                f"Confirm within 24 hours: {signer.link('verify', message_id)}",
                "",
                "If you did not request this, ignore this message. Nothing is sent to an",
                "address that has not been confirmed.",
            )
        ),
    )


def render_alert(
    destination: str,
    public: dict[str, Any],
    *,
    synthetic: bool,
    delivery_id: UUID,
    signer: LinkSigner | None,
) -> Message:
    visitor = _plain(public.get("visitor_name") or public.get("visitor", "unknown visitor"))
    host = _plain(public.get("host_name") or public.get("host", "unknown host"))
    lines = ["SYNTHETIC TEST DATA - not a research or investment signal."] if synthetic else []
    lines.append(f"Reviewed directional signal: {visitor} -> {host}")
    if public.get("as_of"):
        lines.append(f"Feature cutoff: {_plain(public['as_of'])}")
    probability = public.get("calibrated_probability")
    if isinstance(probability, int | float) and not synthetic and 0 <= probability <= 1:
        lines.append(
            f"Calibrated model estimate: {probability:.1%} (model output, not a guarantee)"
        )
    else:
        lines.append("Calibrated probability: unavailable")
    contributions = public.get("contributions")
    if isinstance(contributions, dict) and contributions:
        lines.append("Signed raw-logit contributions:")
        for name, value in sorted(contributions.items()):
            if isinstance(value, int | float):
                lines.append(f"  {_plain(name, 80)}: {value:+.4f}")
    evidence = public.get("evidence")
    if isinstance(evidence, list) and evidence:
        lines.append("Evidence references:")
        lines.extend(f"  - {_plain(item)}" for item in evidence[:20])
    lines += [
        "",
        _plain(
            public.get("statement") or "Observed activity is not proof of a meeting or deal.",
            600,
        ),
    ]
    if signer is not None:
        lines += [
            "",
            f"Dashboard: {signer.base_url}/",
            f"Unsubscribe: {signer.link('unsubscribe', delivery_id)}",
        ]
    return Message(
        destination=destination,
        subject="Tailwatch synthetic test signal" if synthetic else "Tailwatch reviewed signal",
        body="\n".join(lines) + "\n",
    )
