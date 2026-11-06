import logging
import signal
import subprocess
import sys
import time
from uuid import uuid4
from .config import settings
from .db import transaction
from .models import Job, now
from .repository import claim_job, finish_job, LostLease

log = logging.getLogger(__name__)


def main():
    stop = False
    child = None

    def stopping(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    while not stop:
        owner = str(uuid4())
        job_id = claim_job(owner, settings.lease_seconds)
        if not job_id:
            time.sleep(1)
            continue
        child = subprocess.Popen([sys.executable, "-m", "app.crawl", job_id, owner])
        while child.poll() is None:
            try:
                with transaction() as db:
                    job = db.get(Job, job_id)
                    if not job or job.owner != owner:
                        child.terminate()
                        break
                    job.heartbeat = now()
                    if stop:
                        job.status = "pausing"
            except Exception:
                log.exception("Échec du heartbeat ; le lease permettra la reprise.")
            time.sleep(2)
        # Un processus qui s'arrête sans finaliser ne doit pas laisser un job running.
        with transaction() as db:
            job = db.get(Job, job_id)
            incomplete = job and job.owner == owner
        if incomplete:
            try:
                finish_job(
                    job_id,
                    owner,
                    "Moteur arrêté sans finalisation ; reprise manuelle possible.",
                )
            except LostLease:
                pass
    if child and child.poll() is None:
        child.wait(timeout=90)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
