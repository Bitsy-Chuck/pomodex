#!/usr/bin/env python3
"""Spin up a sandbox container and print connection instructions."""

import argparse
import socket
import sys
import uuid

sys.path.insert(0, ".")
from backend.project_service.services.docker_manager import (
    cleanup_project_resources,
    create_container,
    get_container_ip,
)

HOST = socket.gethostname()


def main():
    parser = argparse.ArgumentParser(description="Run a sandbox container")
    parser.add_argument("--name", default=None, help="Project ID (default: random)")
    parser.add_argument("--image", default="agent-sandbox:test", help="Docker image")
    parser.add_argument("--cleanup", metavar="PROJECT_ID", help="Tear down a running sandbox")
    args = parser.parse_args()

    if args.cleanup:
        print(f"Cleaning up sandbox-{args.cleanup} ...")
        cleanup_project_resources(args.cleanup)
        print("Done.")
        return

    project_id = args.name or f"sandbox-{uuid.uuid4().hex[:8]}"

    print(f"Creating sandbox '{project_id}' ...")
    container_id, ssh_port = create_container(project_id, {
        "image": args.image,
        "gcs_bucket": "none",
        "gcs_sa_key": "{}",
        "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeTestKey demo@test",
    })

    bridge_ip = get_container_ip(project_id)

    print()
    print("=" * 60)
    print(f"  Sandbox running: sandbox-{project_id}")
    print(f"  Container ID:    {container_id[:12]}")
    print(f"  Bridge IP:       {bridge_ip}")
    print("=" * 60)
    print()
    print("  SSH (from host):")
    print(f"    ssh -p {ssh_port} agent@localhost")
    print()
    print(f"  ttyd (internal only, not host-mapped):")
    print(f"    http://{bridge_ip}:7681")
    print()
    print("  Cleanup:")
    print(f"    python run_sandbox.py --cleanup {project_id}")
    print()


if __name__ == "__main__":
    main()
