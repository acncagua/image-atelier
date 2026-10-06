"""Read-only, local Booru tag lookup. Never load extension scripts or private lists."""
import bisect
import csv
import functools
import threading
from pathlib import Path

DATA=Path(__file__).with_name('tag_data')
_index=None
_lock=threading.Lock()

def normalized(text):return text.strip().casefold().replace(' ','_').replace('\\(', '(').replace('\\)', ')')

class TagIndex:
    def __init__(self,paths):
        rows={}
        for path in paths:
            extra=path.name=='extra-quality-tags.csv'
            with path.open(encoding='utf-8-sig',newline='') as stream:
                for fields in csv.reader(stream):
                    if len(fields)<2 or not fields[0]:continue
                    name=fields[0]
                    try:category=int(fields[1])
                    except ValueError:continue
                    try:count=int(fields[2]) if len(fields)>2 else 0
                    except ValueError:count=0
                    aliases=tuple(a for a in (fields[3].split(',') if len(fields)>3 else []) if a)
                    prior=rows.get(name)
                    rows[name]={'tag':name,'category':category,'count':max(count,prior['count'] if prior else 0),
                                'aliases':tuple(dict.fromkeys([*(prior['aliases'] if prior else ()),*aliases])),
                                'extra':extra or bool(prior and prior['extra'])}
        self.rows=tuple(rows.values())
        entries=[]
        for i,row in enumerate(self.rows):
            entries.append((normalized(row['tag']),i,''))
            entries.extend((normalized(alias),i,alias) for alias in row['aliases'])
        self.entries=sorted(entries)
        self.keys=[e[0] for e in self.entries]

    @functools.lru_cache(maxsize=256)
    def search(self,query,limit=12):
        q=normalized(query)
        if not 2<=len(q)<=80:return ()
        found={}
        def offer(index,rank,alias=''):
            row=self.rows[index]
            key=(rank,not row['extra'],-row['count'],row['tag'])
            if index not in found or key<found[index][0]:found[index]=(key,alias)
        begin=bisect.bisect_left(self.keys,q);end=bisect.bisect_right(self.keys,q+'\U0010ffff')
        for key,index,alias in self.entries[begin:end]:
            offer(index,0 if key==q and not alias else 1 if not alias else 2,alias)
        if len(found)<limit:
            for i,row in enumerate(self.rows):
                if i in found:continue
                if q in normalized(row['tag']):offer(i,3)
                else:
                    alias=next((a for a in row['aliases'] if q in normalized(a)),None)
                    if alias:offer(i,4,alias)
        selected=sorted(found,key=lambda i:found[i][0])[:limit]
        return tuple((self.rows[i]['tag'],self.rows[i]['category'],self.rows[i]['count'],found[i][1]) for i in selected)

def get_index():
    global _index
    if _index is None:
        with _lock:
            if _index is None:_index=TagIndex([DATA/'danbooru.csv',DATA/'extra-quality-tags.csv'])
    return _index

def suggestions(query,limit=12):
    if not 2<=len(query.strip())<=80:return {'items':[]}
    index=get_index();limit=max(1,min(20,limit))
    return {'items':[{'tag':tag,'category':category,'count':count,'alias':alias} for tag,category,count,alias in index.search(query,limit)],'total_tags':len(index.rows)}
