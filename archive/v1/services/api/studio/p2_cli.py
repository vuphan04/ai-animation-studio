"""P2 episode commands. `preview` only reads SQLite; `preflight` opens UE without writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .service import Studio


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preview", "preflight", "enqueue", "run-once", "jobs", "reconcile", "verify"))
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--project-id")
    parser.add_argument("--episode-id")
    parser.add_argument("--shot-codes", nargs="*")
    parser.add_argument("--job-id")
    parser.add_argument('--worker-stopped', action='store_true', help='confirm old UE worker has stopped before reconcile')
    parser.add_argument("--rerun", action="store_true", help="on a UE test project, apply twice and compare observed fingerprints")
    parser.add_argument("--evidence", type=Path, help="new JSON evidence path; existing files are never replaced")
    args = parser.parse_args()
    studio = Studio(args.data_root, recover_running=False)
    if args.mode in ("preview", "preflight", "enqueue"):
        if not args.project_id or not args.episode_id:
            parser.error("--project-id and --episode-id required")
        call = {"preview": studio.preview_unreal_episode,
                "preflight": studio.preflight_unreal_episode,
                "enqueue": studio.enqueue_unreal_episode}[args.mode]
        result = call(args.project_id, args.episode_id, args.shot_codes)
    elif args.mode == "run-once":
        result = studio.process_next_unreal_episode()
    elif args.mode == "jobs":
        if not args.project_id:
            parser.error("--project-id required")
        result = studio.unreal_jobs(args.project_id)
    elif args.mode == "verify":
        from .p2 import validate_result
        if not args.job_id:
            parser.error("--job-id required")
        with studio.db.connect() as conn:
            row = conn.execute("SELECT * FROM unreal_episode_jobs WHERE id=?", (args.job_id,)).fetchone()
        if row is None:
            parser.error("unknown Unreal job ID")
        stored = json.loads(row["operation_json"])
        operation = studio.preview_unreal_episode(row["project_id"], row["episode_id"], [s["shot_code"] for s in stored["shots"]])
        if operation["operation_id"] != stored["operation_id"]:
            parser.error("episode changed since job; use the original receipt and review current revision")
        adapter = studio._unreal_adapter()
        first = adapter.execute(operation, "observe")
        validate_result(operation, first)
        result = {"kind": "UE_INTEGRATION_OBSERVATION", "job_id": args.job_id,
                  "operation_id": operation["operation_id"], "first": first}
        if args.rerun:
            apply_one = adapter.execute(operation, "apply")
            apply_two = adapter.execute(operation, "apply")
            validate_result(operation, apply_one)
            validate_result(operation, apply_two)
            result["rerun_fingerprints_equal"] = apply_one["output_fingerprint"] == apply_two["output_fingerprint"] == first["output_fingerprint"]
            result["second"] = apply_two
            if not result["rerun_fingerprints_equal"]:
                raise RuntimeError("rerun changed observed output fingerprint")
        if args.evidence:
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            with args.evidence.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
    else:
        if not args.job_id:
            parser.error("--job-id required")
        result = studio.reconcile_unreal_episode(args.job_id, worker_stopped=args.worker_stopped)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
