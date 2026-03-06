from app.observability_config import (
    escape_promql_regex_literal,
    load_observability_config,
    render_query_template,
)


def test_load_observability_config_defaults() -> None:
    config = load_observability_config()
    assert "24h" in config.metrics_allowed_ranges
    assert config.logs_max_lines >= 1
    assert config.timeline_max_points >= 10
    assert 'deployment="{deployment_name}"' in config.metrics_query_uptime_template
    assert 'deployment="{deployment_name}"' in config.timeline_query_availability_template


def test_render_query_template_replaces_variables() -> None:
    rendered = render_query_template(
        'up{namespace="{namespace}", app="{app_label}"}',
        {"namespace": "default", "app_label": "homelab-api"},
        "test",
    )
    assert rendered == 'up{namespace="default", app="homelab-api"}'


def test_escape_promql_regex_literal_keeps_hyphen_and_escapes_regex_metacharacters() -> None:
    assert escape_promql_regex_literal("homelab-api") == "homelab-api"
    assert escape_promql_regex_literal("service.api+(canary)") == r"service\.api\+\(canary\)"
