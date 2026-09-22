import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from persistence import atomic_write,publish_new
file=Path(sys.argv[1]);p=json.loads(file.read_text('utf-8'));folder=file.parent
atomic_write(folder/'status.json',b'{"state":"rewriting"}')
while (Path(p['model'])/'wait').exists():time.sleep(.02)
publish_new(folder/'result.json',json.dumps({'rewritten_prompt':'A detailed illustration of a person in a palace ballroom.','wh_ratio':'2:3'}).encode())
atomic_write(folder/'status.json',b'{"state":"completed"}')
