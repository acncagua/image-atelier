import React,{useEffect,useId,useLayoutEffect,useRef,useState} from 'react';
import {tagContext,insertTag,getTagSuggestions} from './tagCompletion';

const kinds={0:'一般',1:'作者',3:'作品',4:'キャラクター',5:'品質・メタ'};
const countFormat=new Intl.NumberFormat('ja-JP',{notation:'compact',maximumFractionDigits:1});
export default function TagTextarea({value,onChange,enabled=true,...props}){
 const field=useRef(null),selection=useRef(null),composing=useRef(false),list=useRef(null);
 const [caret,setCaret]=useState([0,0]),[focused,setFocused]=useState(false),[items,setItems]=useState([]),[resolvedKey,setResolvedKey]=useState(''),[isComposing,setComposing]=useState(false),[active,setActive]=useState(0),[dismissed,setDismissed]=useState(''),[loading,setLoading]=useState(false),[error,setError]=useState('');
 const id=useId(),context=tagContext(value,caret[0],caret[1]);
 const key=context?.key||'';
 const visible=enabled&&focused&&!isComposing&&context&&key!==dismissed&&resolvedKey===key&&items.length>0;
 useEffect(()=>{
  setItems([]);setLoading(false);setError('');
  if(!enabled||!focused||isComposing||!context||key===dismissed)return;
  let current=true;const controller=new AbortController();
  const timer=setTimeout(async()=>{
   setLoading(true);
   try{const results=await getTagSuggestions(context.query,controller.signal);if(current){setItems(results);setResolvedKey(key);setActive(0);}}
   catch(e){if(current&&e.name!=='AbortError')setError('タグ候補を取得できません。入力は続けられます。');}
   finally{if(current)setLoading(false);}
  },120);
  return()=>{current=false;clearTimeout(timer);controller.abort();};
 },[enabled,focused,key,value,dismissed,isComposing]);
 useLayoutEffect(()=>{
  if(selection.current!==null&&field.current){const position=selection.current;selection.current=null;field.current.focus();field.current.setSelectionRange(position,position);}
 },[value,caret[0],caret[1],items]);
 useEffect(()=>{
  const box=list.current,item=box?.children[active];if(!visible||!item)return;
  if(item.offsetTop<box.scrollTop)box.scrollTop=item.offsetTop;
  else if(item.offsetTop+item.offsetHeight>box.scrollTop+box.clientHeight)box.scrollTop=item.offsetTop+item.offsetHeight-box.clientHeight;
 },[active,visible]);
 function track(e){setCaret([e.currentTarget.selectionStart,e.currentTarget.selectionEnd]);}
 function accept(item){
  const node=field.current,current=node&&tagContext(value,node.selectionStart,node.selectionEnd);
  if(!current||current.key!==key)return;
  const result=insertTag(value,current,item.tag);
  selection.current=result.caret;setCaret([result.caret,result.caret]);setItems([]);setDismissed(tagContext(result.value,result.caret)?.key||'');onChange(result.value);
 }
 function keyDown(e){
  if(composing.current||e.nativeEvent.isComposing||e.keyCode===229)return;
  if(e.key==='Escape'){setDismissed(key);setItems([]);return;}
  if(e.ctrlKey&&e.code==='Space'&&enabled){e.preventDefault();setDismissed('');track(e);return;}
  if(!visible)return;
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setActive(i=>(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length);}
  else if((e.key==='Enter'||e.key==='Tab')&&!e.shiftKey){e.preventDefault();accept(items[active]);}
 }
 return <div className="tag-textarea">
 <textarea {...props} ref={field} value={value} aria-autocomplete={enabled?'list':undefined} aria-controls={visible?id:undefined} aria-activedescendant={visible?id+'-'+active:undefined}
  onFocus={e=>{setFocused(true);track(e);}} onBlur={()=>{setFocused(false);setItems([]);}} onSelect={track} onClick={track}
  onChange={e=>{track(e);setDismissed('');onChange(e.target.value);}} onKeyDown={keyDown}
  onCompositionStart={()=>{composing.current=true;setComposing(true);setItems([]);}} onCompositionEnd={e=>{composing.current=false;setComposing(false);setDismissed('');track(e);}}/>
 {visible?<div className="tag-suggestions"><div className="tag-help">↑↓で選択 · Tab／Enterで入力 · Escで閉じる</div>
 <div role="listbox" ref={list} id={id} aria-label="タグ候補">{items.map((item,index)=><button type="button" role="option" aria-label={item.tag} aria-selected={index===active} id={id+'-'+index} tabIndex={-1} key={item.tag}
  className={'tag-option tag-kind-'+item.category} onPointerDown={e=>e.preventDefault()} onMouseEnter={()=>setActive(index)} onClick={()=>accept(item)}>
  <span>{item.tag}</span><small>{kinds[item.category]||'タグ'}{item.count>0?' · '+countFormat.format(item.count):''}{item.alias?' · 別名: '+item.alias:''}</small>
 </button>)}</div></div>:null}
 {enabled&&focused&&(loading||error)?<small className="tag-feedback" role="status">{error||'タグ候補を検索中…'}</small>:null}
 </div>;
}
