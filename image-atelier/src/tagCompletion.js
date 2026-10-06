// Booru tag boundaries, independent of the textarea and asynchronous suggestions.
export function tagContext(value,caret,end=caret){
 if(caret!==end||caret<0||caret>value.length)return null;
 const left=Math.max(value.lastIndexOf(',',caret-1),value.lastIndexOf('\n',caret-1));
 const stops=[value.indexOf(',',caret),value.indexOf('\n',caret)].filter(n=>n>=0);
 const segmentEnd=stops.length?Math.min(...stops):value.length;
 const segment=value.slice(left+1,segmentEnd);
 const leading=segment.match(/^\s*[\[(]*\s*/)[0];
 const start=left+1+leading.length;
 let stop=segmentEnd;
 const colon=value.indexOf(':',start);
 if(colon>=start&&colon<stop)stop=colon;
 else if(/[\[(]/.test(leading)){
  const closing=segment.match(/(?<!\\)[\])]+\s*$/);
  if(closing)stop=left+1+closing.index;
 }
 while(stop>start&&/\s/.test(value[stop-1]))stop--;
 if(caret<start||caret>stop)return null;
 const query=value.slice(start,caret).trim();
 if(query.length<2||query.length>80||query.startsWith('__')||!/[a-z0-9]/i.test(query)||/[<>\u3000-\u9fff]/.test(query))return null;
 return {start,end:stop,query,segmentEnd,wrapping:/[\[(]/.test(leading),key:JSON.stringify([start,stop,query])};
}

export function insertTag(value,context,tag){
 const text=tag.replaceAll('_',' ').replace(/[()]/g,'\\$&');
 let next=value.slice(0,context.start)+text+value.slice(context.end);
 let caret=context.start+text.length;
 const delta=text.length-(context.end-context.start);
 const segmentEnd=context.segmentEnd+delta;
 if(segmentEnd===next.length){
  // Keep an unfinished weight wrapper intact; append separators after complete tags.
  const suffix=next.slice(caret);
  if(!context.wrapping||/[\])]\s*$/.test(suffix)){next+=', ';caret=next.length;}
 }else if(next[segmentEnd]===','){
  caret=segmentEnd+1;while(caret<next.length&&/[ \t]/.test(next[caret]))caret++;
 }
 return {value:next,caret};
}

const cache=new Map();
export async function getTagSuggestions(query,signal){
 const key=query.toLowerCase();
 if(cache.has(key))return cache.get(key);
 const response=await fetch('/api/tag-completions?'+new URLSearchParams({q:query,limit:12}),{signal});
 if(!response.ok)throw new Error('タグ候補を取得できません。');
 const {items}=await response.json();
 if(!Array.isArray(items))throw new Error('タグ候補の形式が不正です。');
 cache.set(key,items);if(cache.size>100)cache.delete(cache.keys().next().value);
 return items;
}
