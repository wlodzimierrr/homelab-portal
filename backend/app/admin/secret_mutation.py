from __future__ import annotations

import base64

from app.admin.secret_policy import SecretEditTarget, SecretEditingError


def update_secret_manifest_document(
    payload: dict,
    *,
    target: SecretEditTarget,
    secret_key: str,
    secret_value: str,
) -> dict:
    updated = dict(payload)
    field_name = target.encoding_mode
    current = updated.get(field_name)
    if current is None:
        current = {}
    if not isinstance(current, dict):
        raise SecretEditingError(
            f"Secret manifest field {field_name!r} is not a mapping.",
            status_code=502,
        )
    current_mapping = dict(current)

    if target.encoding_mode == "data":
        rendered_value = base64.b64encode(secret_value.encode("utf-8")).decode("ascii")
    else:
        rendered_value = secret_value
    current_mapping[secret_key] = rendered_value
    updated[field_name] = current_mapping
    return updated
