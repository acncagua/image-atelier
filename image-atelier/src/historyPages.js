export const HISTORY_PAGE_SIZE=20;
export function historyPage(items,requested=1){
 const pages=Math.max(1,Math.ceil(items.length/HISTORY_PAGE_SIZE));
 const page=Math.min(pages,Math.max(1,requested));
 const start=(page-1)*HISTORY_PAGE_SIZE;
 return {page,pages,total:items.length,start:items.length?start+1:0,end:Math.min(start+HISTORY_PAGE_SIZE,items.length),items:items.slice(start,start+HISTORY_PAGE_SIZE)};
}
