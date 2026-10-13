export const sketchEnabled=p=>p.model==='SDXL'&&['polish','inpaint'].includes(p.mode)&&p.sketch===true;
export const validSketchColor=value=>typeof value==='string'&&/^#[0-9a-f]{6}$/i.test(value);

export function drawStroke(ctx,stroke,color){
 ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=stroke.width;ctx.lineCap='round';ctx.lineJoin='round';
 ctx.beginPath();stroke.points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.stroke();
 for(const [x,y] of stroke.points){ctx.beginPath();ctx.arc(x,y,stroke.width/2,0,Math.PI*2);ctx.fill();}
}
