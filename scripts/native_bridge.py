#!/usr/bin/env python3
import multiprocessing
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.frozen_runtime import configure_frozen_tls_trust, dispatch_frozen_worker
from scanner.native_bridge import main


if __name__ == "__main__":
    multiprocessing.freeze_support()
    configure_frozen_tls_trust()
    worker_status = dispatch_frozen_worker(sys.argv[1:])
    if worker_status is not None:
        raise SystemExit(worker_status)
    main()
