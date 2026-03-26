def test_scaffold_preview_returns_generated_file_changes(client, monkeypatch) -> None:
    reads: list[tuple[str, str, str]] = []

    class _FakeGitProvider:
        def read_file(self, repo, branch, file_path):
            reads.append((repo, branch, file_path))
            return {
                "environments/dev/workloads/kustomization.yaml": "resources: []\n",
                "bootstrap/project-homelab.yaml": "spec: {}\n",
                "services.yaml": "services: []\n",
            }[file_path]

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.generate_gitops_new_files",
        lambda inp: {f"apps/{inp.name}/{inp.name}-app.yaml": "kind: Application\n"},
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.update_kustomization_resources",
        lambda _raw, new_resource: f"resources:\n- {new_resource}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_catalog_entry_addition",
        lambda _raw, inp: f"services:\n- service_id: {inp.name}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_appproject_addition",
        lambda _raw, inp: f"metadata:\n  name: {inp.name}\n",
    )

    response = client.post(
        "/scaffold/preview",
        json={
            "name": "Demo",
            "description": "Demo service",
            "imageRepo": "ghcr.io/example/demo",
            "repoUrl": "https://github.com/example/demo",
            "ownerEmail": "owner@example.com",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert {(item["path"], item["changeType"]) for item in body["files"]} == {
        ("apps/demo/demo-app.yaml", "create"),
        ("bootstrap/project-homelab.yaml", "modify"),
        ("environments/dev/workloads/kustomization.yaml", "modify"),
        ("services.yaml", "modify"),
    }
    assert reads == [
        (
            "wlodzimierrr/homelab-workloads",
            "main",
            "environments/dev/workloads/kustomization.yaml",
        ),
        ("wlodzimierrr/homelab-workloads", "main", "bootstrap/project-homelab.yaml"),
        ("wlodzimierrr/homelab-workloads", "main", "services.yaml"),
    ]


def test_scaffold_submit_opens_pr_and_returns_commit_summary(
    client, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class _FakeGitProvider:
        def read_file(self, _repo, _branch, file_path):
            return {
                "environments/dev/workloads/kustomization.yaml": "resources: []\n",
                "bootstrap/project-homelab.yaml": "spec: {}\n",
                "services.yaml": "services: []\n",
            }[file_path]

        def create_branch(self, repo, from_branch, new_branch):
            captured["create_branch"] = (repo, from_branch, new_branch)
            return {"branch": new_branch}

        def commit_to_branch(self, repo, branch, files_dict, message):
            captured["commit"] = {
                "repo": repo,
                "branch": branch,
                "files": dict(files_dict),
                "message": message,
            }
            return {"branch": branch}

        def open_pr(self, repo, from_branch, to_branch, title, description):
            captured["pr"] = {
                "repo": repo,
                "from_branch": from_branch,
                "to_branch": to_branch,
                "title": title,
                "description": description,
            }
            return {
                "number": 17,
                "url": "https://github.com/example/homelab-workloads/pull/17",
            }

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.generate_gitops_new_files",
        lambda inp: {f"apps/{inp.name}/{inp.name}-app.yaml": "kind: Application\n"},
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.update_kustomization_resources",
        lambda _raw, new_resource: f"resources:\n- {new_resource}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_catalog_entry_addition",
        lambda _raw, inp: f"services:\n- service_id: {inp.name}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_appproject_addition",
        lambda _raw, inp: f"metadata:\n  name: {inp.name}\n",
    )

    response = client.post(
        "/scaffold/submit",
        json={
            "name": "Demo",
            "description": "Demo service",
            "imageRepo": "ghcr.io/example/demo",
            "repoUrl": "https://github.com/example/demo",
            "ownerEmail": "owner@example.com",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["prUrl"] == "https://github.com/example/homelab-workloads/pull/17"
    assert body["prNumber"] == 17
    assert body["branchName"].startswith("scaffold/demo-")
    assert set(body["filesCommitted"]) == {
        "apps/demo/demo-app.yaml",
        "bootstrap/project-homelab.yaml",
        "environments/dev/workloads/kustomization.yaml",
        "services.yaml",
    }
    assert captured["create_branch"] == (
        "wlodzimierrr/homelab-workloads",
        "main",
        body["branchName"],
    )
    assert captured["pr"] == {
        "repo": "wlodzimierrr/homelab-workloads",
        "from_branch": body["branchName"],
        "to_branch": "main",
        "title": "feat(scaffold): add demo service",
        "description": captured["pr"]["description"],
    }
    assert (
        "Generated by the homelab portal scaffold wizard."
        in captured["pr"]["description"]
    )
    assert captured["commit"] == {
        "repo": "wlodzimierrr/homelab-workloads",
        "branch": body["branchName"],
        "files": {
            "apps/demo/demo-app.yaml": "kind: Application\n",
            "bootstrap/project-homelab.yaml": "metadata:\n  name: demo\n",
            "environments/dev/workloads/kustomization.yaml": "resources:\n- demo-app.yaml\n",
            "services.yaml": "services:\n- service_id: demo\n",
        },
        "message": "feat(scaffold): add demo service manifests and catalog entry",
    }
