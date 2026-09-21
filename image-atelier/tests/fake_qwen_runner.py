"""Local integration fixture; no torch, CUDA or network."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from persistence import atomic_write,publish_new
from imaging import png

file=Path(sys.argv[2]);p=json.loads(file.read_text('utf-8'));folder=file.parent
atomic_write(folder/'status.json',b'{"state":"loading"}')
while (Path(p['model'])/'wait').exists():time.sleep(.02)
result=Image.new('RGBA',(p['width'],p['height']),(32,96,192,255))
publish_new(Path(p['output']),png(result))
atomic_write(folder/'status.json',json.dumps({'state':'completed','environment':{'mock':True},'size':list(result.size)}).encode())
