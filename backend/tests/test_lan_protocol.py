"""Exercise real TLS/MQTT/FTPS clients against an isolated loopback printer."""

import hashlib
import json
import socket
import ssl
import subprocess
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

from dreamer import lan
from dreamer.config import Settings


def listener():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(2)
    server.settimeout(10)
    return server


def test_dispatch_over_real_tls_protocols(tmp_path, monkeypatch):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=TESTSERIAL",
            "-addext",
            "subjectAltName=DNS:TESTSERIAL",
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    settings = Settings(
        _env_file=None,
        printer_transport="lan",
        print_enabled=True,
        printer_host="printer.invalid",
        printer_serial="TESTSERIAL",
        printer_access_code="test-access-code",
        printer_ca_file=str(cert),
    )
    artifact = tmp_path / "print.gcode.3mf"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "G28\n")
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
    mqtt_server, ftp_server = listener(), listener()
    addresses = {8883: mqtt_server.getsockname(), 990: ftp_server.getsockname()}
    connect = socket.create_connection

    def local_only(address, *args, **kwargs):
        if address[0] == "printer.invalid":
            address = addresses[address[1]]
        assert address[0] == "127.0.0.1", "Tests must never reach a physical printer"
        return connect(address, *args, **kwargs)

    monkeypatch.setattr(lan.socket, "create_connection", local_only)
    received = []
    uploaded = threading.Event()

    def mqtt():
        for stage in range(2):
            raw, _ = mqtt_server.accept()
            with context.wrap_socket(raw, server_side=True) as stream:
                client = lan.MQTT(settings)
                client.sock = stream
                kind, body = client.receive(time.monotonic() + 10)
                assert kind == 0x10
                assert b"test-access-code" in body
                stream.sendall(lan._packet(0x20, bytes([0, 0])))
                kind, _ = client.receive(time.monotonic() + 10)
                assert kind == 0x82
                stream.sendall(lan._packet(0x90, bytes([0, 1, 0])))

                def read_request(client=client):
                    kind, body = client.receive(time.monotonic() + 10)
                    assert kind == 0x30
                    length = int.from_bytes(body[:2], "big")
                    assert body[2 : 2 + length] == b"device/TESTSERIAL/request"
                    return json.loads(body[2 + length :])

                def report(value):
                    stream.sendall(
                        lan._packet(
                            0x30,
                            lan._field("device/TESTSERIAL/report")
                            + json.dumps(value).encode(),
                        )
                    )

                assert read_request()["pushing"]["command"] == "pushall"
                report(
                    {
                        "print": {
                            "gcode_state": "IDLE",
                            "nozzle_diameter": "0.4",
                            "sdcard": True,
                            "ams": {
                                "ams": [
                                    {
                                        "id": "0",
                                        "tray": [{"id": "0", "tray_type": "PLA"}],
                                    }
                                ]
                            },
                        }
                    }
                )
                if stage:
                    assert uploaded.is_set()
                    command = read_request()["print"]
                    received.append(command)
                    report(
                        {
                            "print": {
                                "command": "project_file",
                                "sequence_id": "unrelated",
                                "result": "SUCCESS",
                            }
                        }
                    )
                    report(
                        {
                            "print": {
                                "command": "project_file",
                                "sequence_id": command["sequence_id"],
                                "result": "SUCCESS",
                            }
                        }
                    )

    def ftp():
        raw, _ = ftp_server.accept()
        with context.wrap_socket(raw, server_side=True) as stream:
            file = stream.makefile("rb")
            stream.sendall(b"220 Test printer\r\n")
            stored = b""
            data_server = None
            while True:
                line = file.readline().decode().rstrip("\r\n")
                command, _, arg = line.partition(" ")
                if command == "USER":
                    assert arg == "bblp"
                    stream.sendall(b"331 Password required\r\n")
                elif command == "PASS":
                    assert arg == "test-access-code"
                    stream.sendall(b"230 Logged in\r\n")
                elif command in {"PBSZ", "PROT", "TYPE"}:
                    stream.sendall(b"200 OK\r\n")
                elif command == "PASV":
                    data_server = listener()
                    port = data_server.getsockname()[1]
                    stream.sendall(
                        f"227 Passive (127,0,0,1,{port // 256},{port % 256})\r\n".encode()
                    )
                elif command in {"STOR", "RETR"}:
                    assert arg.startswith("dreamer-") and arg.endswith(".gcode.3mf")
                    data_raw, _ = data_server.accept()
                    stream.sendall(b"150 Transfer starting\r\n")
                    with context.wrap_socket(data_raw, server_side=True) as data:
                        assert data.session_reused
                        if command == "STOR":
                            while block := data.recv(8192):
                                stored += block
                        else:
                            data.sendall(stored)
                        data.unwrap().close()
                    data_server.close()
                    stream.sendall(b"226 Transfer complete\r\n")
                elif command == "SIZE":
                    stream.sendall(f"213 {len(stored)}\r\n".encode())
                elif command == "QUIT":
                    assert stored == artifact.read_bytes()
                    uploaded.set()
                    stream.sendall(b"221 Goodbye\r\n")
                    return
                else:
                    raise AssertionError(f"Unexpected FTP command: {command}")

    with mqtt_server, ftp_server, ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(mqtt), pool.submit(ftp)]
        lan.dispatch(settings, artifact)
        for task in tasks:
            task.result(timeout=10)
    assert len(received) == 1
    assert received[0]["md5"] == hashlib.md5(b"G28\n").hexdigest()
