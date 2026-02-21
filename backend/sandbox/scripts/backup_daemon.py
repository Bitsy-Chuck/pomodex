#!/usr/bin/env python3
# /opt/agent/backup_daemon.py
# Periodic rclone sync from /home/agent to GCS. Runs under supervisord.

import subprocess
import time
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

BUCKET = os.environ["GCS_BUCKET"]
PREFIX = os.environ["PROJECT_ID"]
INTERVAL = int(os.environ.get("BACKUP_INTERVAL_SECONDS", "300"))

while True:
    try:
        result = subprocess.run(
            [
                "rclone", "sync", "/home/agent",
                f":gcs:{BUCKET}/projects/{PREFIX}/workspace",
                "--transfers=4", "--checksum",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            logging.info("Backup OK")
        else:
            logging.error(f"Backup failed: {result.stderr}")
    except Exception as e:
        logging.error(f"Backup exception: {e}")
    time.sleep(INTERVAL)
