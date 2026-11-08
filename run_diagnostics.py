"""Persist stage progress before slow work, with periodic stack diagnostics in CI."""
from datetime import datetime, timezone
import faulthandler
import time

from json_persistence import save_json_strict


class RunDiagnostics:
    def __init__(self, path):
        self.path = path
        self.data = {"started_at": self._now(), "status": "running", "stages": []}
        self._save()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _save(self):
        save_json_strict(self.path, self.data)

    def run(self, name, function, *args, **kwargs):
        stage = {"name": name, "status": "running", "started_at": self._now()}
        self.data["stages"].append(stage)
        self._save()
        print(f"Stage start: {name}", flush=True)
        started = time.monotonic()
        # Stack frames expose call sites, not locals or credentials. A killed job
        # retains the last running stage and traces in its Actions logs.
        faulthandler.dump_traceback_later(120, repeat=True)
        try:
            result = function(*args, **kwargs)
            stage["status"] = "completed"
            return result
        except BaseException as exc:
            stage["status"] = "failed"
            stage["error_type"] = type(exc).__name__
            self.data["status"] = "failed"
            raise
        finally:
            faulthandler.cancel_dump_traceback_later()
            stage["elapsed_seconds"] = round(time.monotonic() - started, 3)
            stage["finished_at"] = self._now()
            self._save()
            print(f"Stage {stage['status']}: {name} ({stage['elapsed_seconds']}s)", flush=True)

    def complete(self):
        self.data.update(status="completed", finished_at=self._now())
        self._save()
