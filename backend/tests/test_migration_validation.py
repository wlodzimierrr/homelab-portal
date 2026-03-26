import pytest

from app.migration_validation import (
    MigrationConflict,
    check_argo_app_conflicts,
    check_ingress_conflicts,
    check_pvc_scope,
    check_secret_scope,
    check_service_discovery_references,
    validate_migration,
)


# ---------------------------------------------------------------------------
# validate_migration
# ---------------------------------------------------------------------------


def test_validate_same_namespace_returns_no_conflicts() -> None:
    result = validate_migration(
        service_id="my-svc",
        current_namespace="my-svc",
        target_namespace="my-svc",
        service_manifests={},
        target_project_manifests={},
    )
    assert result.is_safe
    assert result.conflicts == []


def test_validate_simple_service_no_conflicts() -> None:
    deployment = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-svc
  namespace: my-svc
spec:
  template:
    spec:
      containers:
        - name: app
          image: ghcr.io/example/my-svc:latest
"""
    result = validate_migration(
        service_id="my-svc",
        current_namespace="my-svc",
        target_namespace="my-project",
        service_manifests={"apps/my-svc/base/deployment.yaml": deployment},
        target_project_manifests={},
    )
    assert result.is_safe
    assert len(result.conflicts) == 0


# ---------------------------------------------------------------------------
# check_ingress_conflicts
# ---------------------------------------------------------------------------


def _make_ingress(host: str, path: str = "/") -> str:
    return f"""\
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: test-ingress
spec:
  rules:
    - host: {host}
      http:
        paths:
          - path: {path}
            pathType: Prefix
            backend:
              service:
                name: test-svc
                port:
                  number: 80
"""


def test_ingress_no_conflict_different_hosts() -> None:
    source = {"ingress.yaml": _make_ingress("api.example.com")}
    target = {"ingress.yaml": _make_ingress("web.example.com")}
    conflicts = check_ingress_conflicts(source, target)
    assert conflicts == []


def test_ingress_conflict_same_host_and_path() -> None:
    source = {"ingress.yaml": _make_ingress("app.example.com", "/")}
    target = {"ingress.yaml": _make_ingress("app.example.com", "/")}
    conflicts = check_ingress_conflicts(source, target)
    assert len(conflicts) == 1
    assert conflicts[0].kind == "ingress_host_conflict"
    assert conflicts[0].severity == "error"


def test_ingress_no_conflict_same_host_different_path() -> None:
    source = {"ingress.yaml": _make_ingress("app.example.com", "/api")}
    target = {"ingress.yaml": _make_ingress("app.example.com", "/")}
    conflicts = check_ingress_conflicts(source, target)
    assert conflicts == []


# ---------------------------------------------------------------------------
# check_pvc_scope
# ---------------------------------------------------------------------------


def test_pvc_detected() -> None:
    pvc = """\
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: data-pvc
"""
    conflicts = check_pvc_scope({"pvc.yaml": pvc})
    assert len(conflicts) == 1
    assert conflicts[0].kind == "pvc_scope"
    assert conflicts[0].severity == "error"
    assert "data-pvc" in conflicts[0].message


def test_statefulset_volume_claim_template_detected() -> None:
    sts = """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: db
spec:
  volumeClaimTemplates:
    - metadata:
        name: data
"""
    conflicts = check_pvc_scope({"db.yaml": sts})
    assert len(conflicts) == 1
    assert conflicts[0].kind == "pvc_scope"
    assert "data" in conflicts[0].message


def test_no_pvc_no_conflict() -> None:
    deployment = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
"""
    conflicts = check_pvc_scope({"deploy.yaml": deployment})
    assert conflicts == []


# ---------------------------------------------------------------------------
# check_secret_scope
# ---------------------------------------------------------------------------


def test_secret_ref_detected() -> None:
    deployment = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  template:
    spec:
      containers:
        - name: app
          env:
            - name: DB_PASS
              valueFrom:
                secretKeyRef:
                  name: db-credentials
                  key: password
"""
    conflicts = check_secret_scope({"deploy.yaml": deployment}, "target-ns")
    assert len(conflicts) == 1
    assert conflicts[0].kind == "secret_scope"
    assert "db-credentials" in conflicts[0].message


def test_volume_secret_detected() -> None:
    deployment = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  template:
    spec:
      volumes:
        - name: certs
          secret:
            secretName: tls-cert
"""
    conflicts = check_secret_scope({"deploy.yaml": deployment}, "target-ns")
    assert len(conflicts) == 1
    assert "tls-cert" in conflicts[0].message


def test_no_secrets_no_warning() -> None:
    deployment = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
"""
    conflicts = check_secret_scope({"deploy.yaml": deployment}, "target-ns")
    assert conflicts == []


# ---------------------------------------------------------------------------
# check_service_discovery_references
# ---------------------------------------------------------------------------


def test_dns_reference_detected() -> None:
    deployment = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  template:
    spec:
      containers:
        - name: app
          env:
            - name: BACKEND_URL
              value: http://backend.old-ns.svc.cluster.local:8000
"""
    conflicts = check_service_discovery_references(
        {"deploy.yaml": deployment}, "old-ns", "new-ns"
    )
    assert len(conflicts) == 1
    assert conflicts[0].kind == "service_discovery"
    assert "old-ns" in conflicts[0].message


def test_no_dns_reference_no_warning() -> None:
    deployment = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  template:
    spec:
      containers:
        - name: app
          env:
            - name: BACKEND_URL
              value: http://backend:8000
"""
    conflicts = check_service_discovery_references(
        {"deploy.yaml": deployment}, "old-ns", "new-ns"
    )
    assert conflicts == []


# ---------------------------------------------------------------------------
# check_argo_app_conflicts
# ---------------------------------------------------------------------------


def test_argo_conflict_different_apps() -> None:
    conflicts = check_argo_app_conflicts("my-svc-dev", "my-project-dev")
    assert len(conflicts) == 1
    assert conflicts[0].kind == "argo_app_conflict"


def test_argo_no_conflict_same_app() -> None:
    conflicts = check_argo_app_conflicts("my-project-dev", "my-project-dev")
    assert conflicts == []


def test_argo_no_conflict_no_source() -> None:
    conflicts = check_argo_app_conflicts(None, "my-project-dev")
    assert conflicts == []
