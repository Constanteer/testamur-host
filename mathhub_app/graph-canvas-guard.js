/* The graph UI is layered by wrappers. A single render may reach more than one
   mount hook, so make mounting idempotent for the current canvas DOM node. */
const graphCanvasUnguardedMount=mountGraphCanvas;
mountGraphCanvas=function guardedGraphCanvasMount(){
  const canvas=document.querySelector('#argument-graph-canvas-element');
  if(!canvas)return;
  if(canvas.dataset.mathhubGraphMounted==='1'){drawGraphCanvas(canvas);return;}
  canvas.dataset.mathhubGraphMounted='1';
  return graphCanvasUnguardedMount();
};
