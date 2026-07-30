import logging

import telemetry


def test_telemetry_does_not_log_collector_credentials(
    monkeypatch, caplog
) -> None:
    endpoint = "https://collector.example/v1/traces?token=secret"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    monkeypatch.setattr(telemetry, "_configured", False)

    with caplog.at_level(logging.INFO, logger=telemetry.__name__):
        telemetry.telemetry_plugins()

    assert endpoint not in caplog.text
