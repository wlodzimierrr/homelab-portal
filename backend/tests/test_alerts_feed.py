from app.alerts_feed import map_alert_severity, normalize_active_alerts


def test_map_alert_severity_maps_warning_and_critical() -> None:
    assert map_alert_severity("critical") == "critical"
    assert map_alert_severity("error") == "critical"
    assert map_alert_severity("warning") == "warning"
    assert map_alert_severity("info") == "warning"
    assert map_alert_severity("none") is None


def test_normalize_active_alerts_extracts_service_and_env() -> None:
    payload = [
        {
            "status": {"state": "active"},
            "labels": {
                "alertname": "HighLatency",
                "severity": "warning",
                "service": "homelab-api",
                "env": "dev",
            },
            "annotations": {
                "summary": "High latency",
                "description": "P95 exceeded threshold",
            },
            "startsAt": "2026-03-05T10:00:00Z",
        }
    ]

    alerts = normalize_active_alerts(payload)
    assert len(alerts) == 1
    assert alerts[0].title == "High latency"
    assert alerts[0].service_id == "homelab-api"
    assert alerts[0].env == "dev"
    assert alerts[0].severity == "warning"
