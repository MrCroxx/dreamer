"""Verify a built image without provider credentials or printer access."""

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    name = "dreamer-smoke-" + uuid.uuid4().hex[:12]
    volume = name + "-data"
    token = uuid.uuid4().hex
    subprocess.run(
        ["docker", "volume", "create", volume], check=True, capture_output=True
    )
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--init",
                "--read-only",
                "--tmpfs",
                "/tmp:size=256m,mode=1777",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                "4g",
                "-e",
                "DREAMER_API_TOKEN=" + token,
                "-v",
                volume + ":/data",
                "-p",
                "127.0.0.1::8000",
                args.image,
            ],
            check=True,
            capture_output=True,
        )
        port = subprocess.check_output(
            ["docker", "port", name, "8000"], text=True
        ).strip()
        base = "http://" + port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, body=None, authenticated=True):
            headers = {"Authorization": "Bearer " + token} if authenticated else {}
            data = None
            if body is not None:
                headers["Content-Type"] = "application/json"
                data = json.dumps(body).encode()
            with opener.open(
                urllib.request.Request(base + path, data=data, headers=headers),
                timeout=10,
            ) as response:
                return response.read()

        def ready():
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    request("/healthz", authenticated=False)
                    return
                except (OSError, urllib.error.URLError):
                    time.sleep(0.5)
            raise RuntimeError("Container did not become ready")

        ready()
        assert b"Dreamer" in request("/", authenticated=False)
        try:
            request("/api/status", authenticated=False)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("Container allowed an unauthenticated API request")
        status = json.loads(request("/api/status"))
        assert not status["print_ready"]
        job = json.loads(
            request(
                "/api/jobs",
                {"prompt": "Container smoke test", "demo_template": "planter"},
            )
        )
        endpoint = "/api/jobs/" + job["id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job = json.loads(request(endpoint))
            if job["state"] == "preview":
                break
            assert job["state"] != "failed", job["error"]
            time.sleep(0.5)
        assert job["state"] == "preview", job
        assert job["geometry"]["dimensions"] == [72, 72, 65]
        assert len(request(endpoint + "/files/model.stl")) > 100
        assert b"ISO-10303-21" in request(endpoint + "/files/model.step")
        uid = subprocess.check_output(
            ["docker", "exec", name, "id", "-u"], text=True
        ).strip()
        assert uid == "10001", uid
        subprocess.run(["docker", "restart", name], check=True, capture_output=True)
        ready()
        restored = json.loads(request(endpoint))
        assert restored["state"] == "preview"
        assert restored["model_sha256"] == job["model_sha256"]
        print(
            "Container smoke passed: UI, authentication, CAD exports, non-root user, restart persistence."
        )
    except Exception:
        subprocess.run(["docker", "logs", "--tail", "60", name], check=False)
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
        subprocess.run(
            ["docker", "volume", "rm", volume], check=False, capture_output=True
        )


if __name__ == "__main__":
    main()
