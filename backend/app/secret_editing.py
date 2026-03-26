from __future__ import annotations

from app.admin.secret_mutation import update_secret_manifest_document
from app.admin.secret_policy import (
    SECRET_EDIT_TARGETS,
    SecretEditTarget,
    SecretEditingError,
    clear_secret_edit_rate_limit_state_for_tests,
    enforce_secret_edit_rate_limit,
    resolve_secret_edit_target,
)
from app.admin.secret_runtime import (
    DEFAULT_WORKLOADS_REPO_ROOT,
    decrypt_secret_manifest,
    encrypt_secret_manifest,
    normalize_sops_config_contents,
)

_normalize_sops_config_contents = normalize_sops_config_contents
