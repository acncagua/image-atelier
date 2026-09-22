"""Called under store.gpu_execution; selected flag may change during inference."""
import json
import time
import uuid
from pathlib import Path
from managed_child import launch, reap_tree
from persistence import atomic_write


class Session:
    def __init__(self, store):
        self.folder = store.path / 'qwen-resident'
        self.process = None
        self.key = None
        self.selected = True

    def unload(self):
        if self.process:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=10)
            if hasattr(self.process, 'close'):
                self.process.close()
            self.process = None
        self.key = None

    def start(self, machine, runner, request, env):
        data = json.loads(request.read_text('utf-8'))
        key = (machine['python'], str(runner), *(data.get(k) for k in
               ('model','offload','vae_tiling','vae_tile_size','vae_tile_stride')))
        if self.key != key or not self.process or self.process.poll() is not None:
            self.unload()
            self.folder.mkdir(parents=True, exist_ok=True)
            # Publish a fresh command before launch; never replay a stale request.
            ident = uuid.uuid4().hex
            atomic_write(self.folder/'command.json', json.dumps({'id':ident,'request':str(request.resolve())}).encode())
            root = Path(__file__).resolve().parent
            self.process = launch([machine['python'],'-I',str(root/'qwen_resident.py'),str(self.folder.resolve()),str(runner)], self.folder, root, env)
            self.key = key
        else:
            ident = uuid.uuid4().hex
            atomic_write(self.folder/'command.json', json.dumps({'id':ident,'request':str(request.resolve())}).encode())
        return Invocation(self, ident)


class Invocation:
    def __init__(self, session, ident):
        self.session, self.ident = session, ident

    def poll(self):
        try:
            done = json.loads((self.session.folder/'done.json').read_text('utf-8'))
            if done['id'] == self.ident:
                return done['code']
        except (OSError, ValueError, KeyError):
            pass
        if not self.session.process or self.session.process.poll() is not None:
            return 1
        return None

    def kill(self):
        self.session.unload()

    def wait(self, timeout=10):
        deadline = time.monotonic()+timeout
        while self.poll() is None:
            if time.monotonic()>deadline:
                raise TimeoutError('Qwen completion timed out')
            time.sleep(.02)
        return self.poll()

    def close(self):
        if self.poll() != 0 or not self.session.selected:
            self.session.unload()


def unload(store):
    session = getattr(store, 'qwen_session', None)
    if session:
        session.unload()


def recover(store):
    reap_tree(store.path/'qwen-resident')
