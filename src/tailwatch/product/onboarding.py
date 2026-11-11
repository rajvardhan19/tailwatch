"""Local operator provisioning; secrets are written once to an owner-only file.

The operator receives only the customer API key. Recipient verification and
unsubscribe secrets are delivered to the recipient through signed message links,
so an operator cannot confirm a destination on the recipient's behalf.
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from tailwatch.product.api import issue_key
from tailwatch.product.delivery import request_verification, subscribe


def provision_workspace(engine: Engine, *, name: str, email: str, output: Path) -> dict[str, str]:
    if not name.strip() or len(name) > 200:
        raise ValueError("workspace name required (maximum 200 characters)")
    # O_EXCL also rejects existing symlinks. Never overwrite a prior credential.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    tenant = uuid4()
    try:
        with os.fdopen(descriptor, "w") as secret_file, engine.begin() as conn:
            conn.execute(
                text("INSERT INTO tenants(tenant_id,name) VALUES(:id,:name)"),
                {"id": tenant, "name": name},
            )
            key = issue_key(conn, tenant, "customer")
            subscription, _, _ = subscribe(conn, tenant, email)
            message = request_verification(conn, subscription, "provision-workspace")
            json.dump(
                {
                    "tenant_id": str(tenant),
                    "customer_key": key,
                    "subscription_id": str(subscription),
                    "destination": email,
                    "verification_message_id": str(message),
                    "verified": False,
                    "delivery_enabled": False,
                },
                secret_file,
                sort_keys=True,
            )
            secret_file.flush()
            os.fsync(secret_file.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {
        "tenant_id": str(tenant),
        "subscription_id": str(subscription),
        "verification_message_id": str(message),
        "secret_file": str(output),
    }


def issue_key_file(
    engine: Engine, *, tenant: UUID, scope: Literal["customer", "analyst"], output: Path
) -> dict[str, str]:
    """Issue an additional key (e.g. analyst access or rotation) into an owner-only file."""
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as secret_file, engine.begin() as conn:
            if not conn.execute(
                text("SELECT 1 FROM tenants WHERE tenant_id=:id"), {"id": tenant}
            ).first():
                raise ValueError("unknown tenant")
            key = issue_key(conn, tenant, scope)
            json.dump({"tenant_id": str(tenant), "scope": scope, "key": key}, secret_file)
            secret_file.flush()
            os.fsync(secret_file.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {
        "tenant_id": str(tenant),
        "scope": scope,
        # The digest identifies the key for later revocation; it cannot authenticate.
        "key_hash": hashlib.sha256(key.encode()).hexdigest(),
        "secret_file": str(output),
    }


def revoke_key(engine: Engine, key_hash: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", key_hash):
        raise ValueError("SHA-256 key digest required")
    with engine.begin() as conn:
        if not conn.execute(
            text(
                "UPDATE api_keys SET revoked=true WHERE key_hash=:hash AND NOT revoked "
                "RETURNING key_hash"
            ),
            {"hash": key_hash},
        ).first():
            raise ValueError("unknown or already revoked key")
