import json
import socket
import ssl
import time
import zipfile

import pytest

from dreamer.config import Settings
from dreamer.lan import MQTT, _field, _packet, dispatch, tls_context
from dreamer.profiles import flatten
from dreamer.service import Service


@pytest.fixture
def config(tmp_path):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        print_enabled=True,
        printer_transport="lan",
        printer_serial="TESTSERIAL",
        printer_host="printer.invalid",
        printer_access_code="test-device-secret",
    )


@pytest.fixture
def sliced(tmp_path):
    path = tmp_path / "print.gcode.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "; test fixture\nG28\n")
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps(
                {
                    "printer_model": "Bambu Lab A1 mini",
                    "nozzle_diameter": ["0.4"],
                    "filament_type": ["PLA"],
                    "curr_bed_type": "Textured PEI Plate",
                }
            ),
        )
    return path


def idle():
    return {
        "gcode_state": "IDLE",
        "sdcard": True,
        "nozzle_diameter": "0.4",
        "print_error": 0,
        "hms": [],
        "ams": {"ams": [{"id": "0", "tray": [{"id": "0", "tray_type": "PLA"}]}]},
    }


def transports(statuses, *, upload_error=None, start_error=None):
    events = []

    class FakeMQTT:
        def __init__(self, _):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def status(self):
            events.append("status")
            return statuses.pop(0)

        def start(self, payload):
            events.append(payload)
            if start_error:
                raise start_error

    def upload(_, path, remote):
        events.append(("upload", remote))
        if upload_error:
            raise upload_error

    return FakeMQTT, upload, events


@pytest.mark.parametrize("state", ["RUNNING", "PAUSE", "PREPARE", "FAILED", None])
def test_busy_or_unknown_printer_is_never_uploaded_to(config, sliced, state):
    status = idle()
    status["gcode_state"] = state
    mqtt, upload, events = transports([status])
    with pytest.raises(ValueError, match="busy"):
        dispatch(config, sliced, mqtt_factory=mqtt, uploader=upload)
    assert events == ["status"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("nozzle_diameter", "0.6"),
        ("sdcard", False),
        ("print_error", 123),
        ("hms", [{"code": 1}]),
        ("ams", {}),
    ],
)
def test_incompatible_or_missing_telemetry_blocks_upload(config, sliced, field, value):
    status = idle()
    status[field] = value
    mqtt, upload, events = transports([status])
    with pytest.raises(ValueError):
        dispatch(config, sliced, mqtt_factory=mqtt, uploader=upload)
    assert events == ["status"]


def test_printer_becoming_busy_during_upload_blocks_start(config, sliced):
    busy = idle()
    busy["gcode_state"] = "RUNNING"
    mqtt, upload, events = transports([idle(), busy])
    with pytest.raises(ValueError, match="busy"):
        dispatch(config, sliced, mqtt_factory=mqtt, uploader=upload)
    assert len(events) == 3
    assert events[-1] == "status"


def test_verified_upload_precedes_single_start(config, sliced):
    mqtt, upload, events = transports([idle(), idle()])
    dispatch(config, sliced, mqtt_factory=mqtt, uploader=upload)
    assert len(events) == 4
    assert events[0] == events[2] == "status"
    payload = events[3]["print"]
    assert payload["url"] == "ftp:///" + events[1][1]
    assert payload["ams_mapping"] == [0]
    assert payload["bed_leveling"] is True
    assert payload["param"] == "Metadata/plate_1.gcode"
    assert len(payload["md5"]) == 32


def test_transfer_failure_never_sends_print_command(config, sliced):
    mqtt, upload, events = transports(
        [idle()], upload_error=ValueError("checksum mismatch")
    )
    with pytest.raises(ValueError, match="checksum"):
        dispatch(config, sliced, mqtt_factory=mqtt, uploader=upload)
    assert len(events) == 2


def test_timeout_after_publish_is_not_retried(config, sliced):
    mqtt, upload, events = transports([idle(), idle()], start_error=TimeoutError())
    with pytest.raises(TimeoutError):
        dispatch(config, sliced, mqtt_factory=mqtt, uploader=upload)
    assert sum(isinstance(event, dict) for event in events) == 1


def test_disabled_dispatch_does_not_open_connections(config, sliced):
    config.print_enabled = False
    mqtt, upload, events = transports([])
    with pytest.raises(ValueError, match="disabled"):
        dispatch(config, sliced, mqtt_factory=mqtt, uploader=upload)
    assert events == []


def test_mismatched_plate_is_rejected_before_network(config, sliced):
    config.bed_type = "Cool Plate"
    mqtt, upload, events = transports([])
    with pytest.raises(ValueError, match="plate"):
        dispatch(config, sliced, mqtt_factory=mqtt, uploader=upload)
    assert events == []


def test_mqtt_accepts_only_matching_success_ack(config):
    mqtt = MQTT(config)
    published = []
    mqtt.publish = published.append
    reports = iter(
        [
            {
                "print": {
                    "command": "project_file",
                    "sequence_id": "other",
                    "result": "SUCCESS",
                }
            },
            {"print": {"gcode_state": "RUNNING"}},
            {
                "print": {
                    "command": "project_file",
                    "sequence_id": "123",
                    "result": "SUCCESS",
                }
            },
        ]
    )
    mqtt.report = lambda deadline: next(reports)
    mqtt.start({"print": {"sequence_id": "123"}})
    assert len(published) == 1


def test_mqtt_rejection_is_not_success(config):
    mqtt = MQTT(config)
    mqtt.publish = lambda payload: None
    mqtt.report = lambda deadline: {
        "print": {
            "command": "project_file",
            "sequence_id": "123",
            "result": "FAIL",
        }
    }
    with pytest.raises(ValueError, match="rejected"):
        mqtt.start({"print": {"sequence_id": "123"}})


def test_mqtt_framing_ignores_retained_status_and_handles_large_packets(config):
    mqtt = MQTT(config)
    left, right = socket.socketpair()
    try:
        mqtt.sock = left
        topic = _field("device/TESTSERIAL/report")
        stale = _packet(
            0x31, topic + json.dumps({"print": {"gcode_state": "IDLE"}}).encode()
        )
        fresh = {"print": {"gcode_state": "RUNNING", "padding": "x" * 300}}
        right.sendall(stale + _packet(0x30, topic + json.dumps(fresh).encode()))
        assert mqtt.report(time.monotonic() + 1) == fresh
    finally:
        left.close()
        right.close()


def test_printer_tls_checks_ca_and_identity(config):
    context = tls_context(config)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.cert_store_stats()["x509_ca"] > 0


def test_device_credentials_are_redacted(config):
    service = Service(config, None)
    message = service.error(
        ValueError(
            f"{config.printer_host} {config.printer_serial} {config.printer_access_code}"
        )
    )
    assert message == "[redacted] [redacted] [redacted]"


def test_preset_resolver_keeps_start_code_and_leaf_overrides(tmp_path):
    root = tmp_path / "machine"
    root.mkdir()
    presets = [
        {"name": "base", "speed": "10", "machine_end_gcode": "M84"},
        {"name": "start", "machine_start_gcode": "G28"},
        {"name": "printer", "inherits": "base", "include": ["start"], "speed": "20"},
    ]
    for p in presets:
        (root / (p["name"] + ".json")).write_text(json.dumps(p))
    output = flatten(tmp_path, "machine", "printer")
    assert output["machine_start_gcode"] == "G28"
    assert output["machine_end_gcode"] == "M84"
    assert output["speed"] == "20"
    assert output["name"] == "printer"
    assert "inherits" not in output and "include" not in output
    (root / "base.json").write_text(json.dumps({"name": "base", "inherits": "printer"}))
    with pytest.raises(ValueError, match="cycle"):
        flatten(tmp_path, "machine", "printer")
