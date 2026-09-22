"""One owned subprocess, one pipeline, serial file-based requests (no listener)."""
import importlib.util
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from persistence import atomic_write


def serve(folder, runner):
    spec = importlib.util.spec_from_file_location('resident_runner', runner)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cache = {}
    previous = None
    while True:
        try:
            command = json.loads((folder / 'command.json').read_text('utf-8'))
        except (OSError, ValueError):
            time.sleep(.05)
            continue
        if command['id'] == previous:
            time.sleep(.05)
            continue
        previous = command['id']
        code = module.execute(Path(command['request']), cache)
        atomic_write(folder / 'done.json', json.dumps({'id':previous,'code':code}).encode())
        # Failed inference may leave hooks or CUDA state unusable. Never reuse it.
        if code:
            return code


if __name__ == '__main__':
    raise SystemExit(serve(Path(sys.argv[1]), Path(sys.argv[2])))
