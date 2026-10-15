"""Command line tools.

    python -m callcenter seed --count 40     # generate + process sample calls (local mode)
    python -m callcenter upload              # upload the 4 demo recordings (Data/demo_calls); local or Azure
    python -m callcenter process path/to/call.wav [--meta meta.json]
    python -m callcenter worker              # watch the recordings container and process new files
    python -m callcenter retry CALL-...
    python -m callcenter status
    python -m callcenter reset               # delete all local data (local mode only)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

from .config import get_settings
from .factory import get_blob_store, get_call_store


def cmd_seed(args) -> int:
    from . import pipeline, samples

    s = get_settings()
    blobs = get_blob_store()
    calls = samples.generate_calls(count=args.count, days=args.days, seed=args.seed)
    print(f"Uploading {len(calls)} sample calls to '{s.container_recordings}'...")
    for i, (call_id, meta, wav) in enumerate(calls, 1):
        blobs.put_json(s.container_recordings, f"{call_id}.json", meta)  # sidecar first
        blobs.put_bytes(s.container_recordings, f"{call_id}.wav", wav, "audio/wav")
        if s.mode == "azure" and not args.process:
            continue  # Event Grid triggers the Functions
        try:
            doc = pipeline.process_recording(f"{call_id}.wav")
            print(f"  [{i}/{len(calls)}] {call_id}  QA {doc['qa']['percent']:5.1f}%  "
                  f"{doc['sentiment']['customer']:<8} {doc['resolution']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(calls)}] {call_id}  FAILED: {exc}")
    return 0


def cmd_upload(args) -> int:
    """Upload recordings (+ same-name .json metadata) to the recordings container.

    Local mode: processed right away. Azure mode: Event Grid triggers the Functions.
    """
    from datetime import datetime, timedelta, timezone

    from . import pipeline

    s = get_settings()
    blobs = get_blob_store()
    paths = [Path(p) for p in (args.paths or [str(s.data_dir / "demo_calls")])]
    files = sorted(f for p in paths for f in ([p] if p.is_file() else p.iterdir()) if pipeline.is_audio(f.name))
    if not files:
        print("No audio files found", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for i, audio in enumerate(files):
        sidecar = audio.with_suffix(".json")
        meta = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
        meta.setdefault("started_at", (now - timedelta(hours=len(files) - i)).isoformat())
        if args.agent_email and i == 0:
            meta["agent_email"] = args.agent_email  # lets that user test the Agent ("My scorecard") view
        blobs.put_json(s.container_recordings, f"{audio.stem}.json", meta)  # metadata first
        blobs.put_bytes(s.container_recordings, audio.name, audio.read_bytes())
        if s.mode == "azure" and not args.process:
            print(f"Uploaded {audio.name} -> processing in Azure Functions")
            continue
        doc = pipeline.process_recording(audio.name, force=True)
        print(f"Processed {audio.name}: QA {doc['qa']['percent']}%  {doc['resolution']}  flags={doc['flag_list']}")
    return 0


def cmd_process(args) -> int:
    from . import pipeline

    s = get_settings()
    path = Path(args.file)
    blobs = get_blob_store()
    if args.meta:
        blobs.put_bytes(s.container_recordings, f"{path.stem}.json", Path(args.meta).read_bytes(), "application/json")
    blobs.put_bytes(s.container_recordings, path.name, path.read_bytes())
    doc = pipeline.process_recording(path.name)
    print(json.dumps({k: doc[k] for k in ("call_id", "summary", "call_reason", "resolution", "qa", "flag_list")},
                     indent=2, default=str))
    return 0


def cmd_worker(args) -> int:
    from . import pipeline

    s = get_settings()
    blobs, store = get_blob_store(), get_call_store()
    print(f"Watching '{s.container_recordings}' every {args.interval}s (Ctrl+C to stop)")
    while True:
        for name in blobs.list(s.container_recordings):
            if not pipeline.is_audio(name):
                continue
            doc = store.get(pipeline.call_id_for(name))
            if doc and doc.get("status") != pipeline.STATUS_UPLOADED:
                continue
            print(f"Processing {name}")
            try:
                pipeline.process_recording(name)
            except Exception as exc:  # noqa: BLE001
                print(f"  failed: {exc}")
        time.sleep(args.interval)


def cmd_retry(args) -> int:
    from . import pipeline

    doc = pipeline.retry(args.call_id)
    print(f"{args.call_id}: {doc['status'] if doc else 'skipped'}")
    return 0


def cmd_status(args) -> int:
    items = get_call_store().list_summaries()
    counts = Counter(c.get("status") for c in items)
    print(f"{len(items)} calls: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for c in items:
        if c.get("status") == "failed":
            print(f"  FAILED {c['call_id']} [{c.get('stage')}] {c.get('error')}")
    return 0


def cmd_reset(args) -> int:
    s = get_settings()
    if s.storage_backend != "local" or s.store_backend != "local":
        print("reset only works in local mode", file=sys.stderr)
        return 1
    import shutil

    shutil.rmtree(s.local_data_dir, ignore_errors=True)
    print(f"Deleted {s.local_data_dir}")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="callcenter", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed", help="generate and process synthetic sample calls")
    p.add_argument("--count", type=int, default=40)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--process", action="store_true", help="in azure mode, also process in-process instead of via Functions")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("upload", help="upload recordings + metadata (default: the 4 demo calls)")
    p.add_argument("paths", nargs="*", help="audio files or folders (default Data/demo_calls)")
    p.add_argument("--agent-email", help="assign the first call to this agent email (to test the Agent view)")
    p.add_argument("--process", action="store_true", help="in azure mode, process in-process instead of via Functions")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("process", help="upload and process one audio file")
    p.add_argument("file")
    p.add_argument("--meta", help="metadata JSON (agent_id, agent_name, team, started_at, ...)")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("worker", help="poll the recordings container and process new audio")
    p.add_argument("--interval", type=int, default=5)
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("retry", help="re-run a failed call")
    p.add_argument("call_id")
    p.set_defaults(func=cmd_retry)

    sub.add_parser("status", help="show processing status").set_defaults(func=cmd_status)
    sub.add_parser("reset", help="delete all local data").set_defaults(func=cmd_reset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
