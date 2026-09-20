export function forecastCost(jobs,p){
 const samples=jobs.filter(j=>j.params.provider==='openai'&&j.status==='completed'&&Number.isFinite(j.estimate)&&j.estimate>=0&&['model','quality','width','height','mode'].every(k=>j.params[k]===p[k])).slice(0,10);
 if(!samples.length)return null;
 return {amount:samples.reduce((sum,j)=>sum+j.estimate/(j.params.n||1),0)/samples.length*p.n,samples:samples.length};
}
