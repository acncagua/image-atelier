// A preview is valid only for the exact inputs from which it was generated.
export function promptInputKey(parameters,references,targetId,strokes){
 return JSON.stringify({parameters,references,targetId,strokes});
}
