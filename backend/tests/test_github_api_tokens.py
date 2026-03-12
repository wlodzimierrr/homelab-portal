from app import main


def test_github_api_token_for_package_paths_prefers_ghcr_token(monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_GITHUB_ACTIONS_TOKEN", "actions-token")
    monkeypatch.setenv("GHCR_READ_TOKEN", "ghcr-token")

    assert (
        main._github_api_token_for_path("users/wlodzimierrr/packages/container/homelab-web/versions")
        == "ghcr-token"
    )


def test_github_api_token_for_non_package_paths_uses_metadata_token(monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_GITHUB_ACTIONS_TOKEN", "actions-token")
    monkeypatch.delenv("GHCR_READ_TOKEN", raising=False)

    assert main._github_api_token_for_path("repos/wlodzimierrr/homelab-workloads/pulls") == "actions-token"
