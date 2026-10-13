/* Give the interactive shell a paint before doing background map work. */
(function(global){
 'use strict';
 let held=false,lastInput=0;
 for(const event of ['pointerdown','pointerup','pointercancel','keydown'])document.addEventListener(event,()=>{held=event==='pointerdown';lastInput=Date.now();},{capture:true,passive:true});
 global.addEventListener('blur',()=>{held=false;});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)held=false;});
 function afterPaint(){return new Promise(resolve=>global.requestAnimationFrame(()=>global.requestAnimationFrame(()=>setTimeout(resolve,0))));}
 async function mapIdle(delay=0){
  await afterPaint();if(delay)await new Promise(resolve=>setTimeout(resolve,delay));
  return new Promise(resolve=>{
   const check=()=>{
    if(document.hidden||held||Date.now()-lastInput<250||document.querySelector('.map-overlay-controls details[open],#map-controls[open],dialog[open]')){setTimeout(check,150);return;}
    resolve();
   };
   if(global.requestIdleCallback)global.requestIdleCallback(check,{timeout:1000});else setTimeout(check,0);
  });
 }
 global.PhotoScoutStartup={afterPaint,mapIdle};
})(window);
