from app.observability_config import load_observability_config, render_query_template


def test_load_observability_config_defaults() -> None:
    config = load_observability_config()
    assert "24h" in config.metrics_allowed_ranges
    assert config.logs_max_lines >= 1
    assert config.timeline_max_points >= 10


def test_render_query_template_replaces_variables() -> None:
    rendered = render_query_template(
        'up{namespace="{namespace}", app="{app_label}"}',
        {"namespace": "default", "app_label": "homelab-api"},
        "test",
    )
    assert rendered == 'up{namespace="default", app="homelab-api"}'
