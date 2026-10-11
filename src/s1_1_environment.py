"""Section 1.1: record hardware, software versions and PostgreSQL settings.

Writes results/environment.json and prints a summary for the screenshot.
"""
import json
import platform
import shutil
import subprocess

from common import RESULTS, cat_text, connect


def run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as exc:  # tool missing on this OS
        return f"unavailable ({exc.__class__.__name__})"


def mac_storage() -> str:
    info = run(["diskutil", "info", "/"])
    solid = [l.split(":", 1)[1].strip() for l in info.splitlines() if "Solid State" in l]
    proto = [l.split(":", 1)[1].strip() for l in info.splitlines() if "Protocol" in l]
    return f"{'SSD' if solid and solid[0] == 'Yes' else 'HDD/unknown'} ({proto[0] if proto else '?'})"


def main() -> None:
    env = {"recorded_at_cat": cat_text()}
    if platform.system() == "Darwin":
        env["cpu_model"] = run(["sysctl", "-n", "machdep.cpu.brand_string"])
        env["cpu_cores"] = run(["sysctl", "-n", "hw.ncpu"])
        env["ram_gb"] = round(int(run(["sysctl", "-n", "hw.memsize"])) / 1024**3, 1)
        env["os"] = f"macOS {platform.mac_ver()[0]}"
        env["storage"] = mac_storage()
    else:
        env["cpu_model"] = platform.processor()
        env["os"] = platform.platform()
    env["python"] = platform.python_version()

    with connect() as conn:
        cur = conn.cursor()
        env["postgresql"] = cur.execute("SHOW server_version").fetchone()[0]
        env["timescaledb"] = cur.execute(
            "SELECT extversion FROM pg_extension WHERE extname='timescaledb'").fetchone()[0]
        env["session_time_zone"] = cur.execute("SHOW TimeZone").fetchone()[0]
        for name in ("shared_buffers", "work_mem", "max_parallel_workers_per_gather", "jit"):
            env[name] = cur.execute(f"SHOW {name}").fetchone()[0]

    if shutil.which("docker"):
        env["emqx"] = run(["docker", "exec", "emqx", "emqx", "ctl", "broker"]).replace("\n", "; ")
        env["postgresql_runtime"] = "Docker container (timescale/timescaledb:latest-pg17) " \
                                    "inside the Docker Desktop Linux VM"
        env["docker_vm_limits"] = run(["docker", "info", "--format",
                                       "{{.NCPU}} CPUs, {{.MemTotal}} bytes RAM"])
        env["container_limits"] = run(["docker", "inspect", "tsdb", "--format",
                                       "NanoCpus={{.HostConfig.NanoCpus}} "
                                       "Memory={{.HostConfig.Memory}} (0 = no per-container limit)"])

    (RESULTS / "environment.json").write_text(json.dumps(env, indent=2))
    width = max(map(len, env))
    for key, value in env.items():
        print(f"{key.ljust(width)} : {value}")


if __name__ == "__main__":
    main()
