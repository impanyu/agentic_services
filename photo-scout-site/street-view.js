// A shared controller for Google Street View's heading, pitch and field of view.
(function(global){
 const clamp=(n,min,max)=>Math.max(min,Math.min(max,n));
 function view(spot){const q=new URL(spot.sourceUrl).searchParams;return {heading:Number(spot.viewHeadingDegrees??q.get('heading')??0),pitch:Number(spot.viewPitchDegrees??q.get('pitch')??0),fov:Number(spot.viewFovDegrees??q.get('fov')??120)};}
 function adjust(current,delta,round=true){const quantize=round?Math.round:n=>n;return {heading:quantize(((current.heading+(delta.heading||0))%360+360)%360)%360,pitch:quantize(clamp(current.pitch+(delta.pitch||0),-90,90)),fov:quantize(clamp(current.fov+(delta.fov||0),30,120))};}
 function keyDelta(key){return {ArrowLeft:{heading:-15},ArrowRight:{heading:15},ArrowUp:{pitch:10},ArrowDown:{pitch:-10},'+':{fov:-10},'=':{fov:-10},'-':{fov:10},'_':{fov:10}}[key];}
 function bind(container,{getSpot,onChange,onCommit,isLive=()=>false,isDisabled=()=>false}){
  container.classList.add('street-view-preview');container.tabIndex=0;container.setAttribute('role','group');container.setAttribute('aria-label','Adjust Street View: drag to look around, arrow keys to turn and tilt, plus or minus to zoom');
  const tools=document.createElement('div');tools.className='street-view-tools';
  const caption=document.createElement('span');caption.className='street-view-caption';caption.setAttribute('aria-live','polite');
  const hint=document.createElement('span');hint.className='street-view-gesture-hint';hint.textContent='Drag to look · + / − to zoom';
  const zoom=document.createElement('div');zoom.className='street-view-zoom';
  let timer,drag=null,suppressClick=false,lastPreview=0;
  function refresh(){const spot=getSpot(),enabled=spot?.provider==='google-street-view';container.classList.toggle('street-view-active',enabled);tools.hidden=hint.hidden=!enabled;if(enabled){const v=view(spot);caption.textContent=`${Math.round(v.heading)}° · tilt ${Math.round(v.pitch)}° · FOV ${Math.round(v.fov)}°`;}}
  function commit(){clearTimeout(timer);timer=null;if(container.isConnected===false)return;if(getSpot()?.provider==='google-street-view'&&!isDisabled())onCommit();}
  // A live sphere paints from onChange; only static-image fallback needs timed commits.
  function preview(){if(drag&&isLive()){clearTimeout(timer);timer=null;return;}const now=Date.now();if(!lastPreview||now-lastPreview>=180){lastPreview=now;commit();}else if(!timer)timer=setTimeout(()=>{timer=null;lastPreview=Date.now();commit();},180-(now-lastPreview));}
  function change(delta,delay=true){if(getSpot()?.provider!=='google-street-view'||isDisabled())return false;onChange(adjust(view(getSpot()),delta));refresh();if(delay)preview();return true;}
  for(const [text,label,delta] of [['+','Zoom in',{fov:-10}],['−','Zoom out',{fov:10}]]){const b=document.createElement('button');b.type='button';const icon=document.createElement('span');icon.className=text==='+'?'street-view-zoom-symbol street-view-zoom-plus':'street-view-zoom-symbol';icon.setAttribute('aria-hidden','true');b.append(icon);b.setAttribute('aria-label',label);b.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();change(delta);});zoom.append(b);}
  tools.append(caption,zoom);container.append(tools,hint);
  container.addEventListener('keydown',event=>{const delta=keyDelta(event.key);if(delta&&change(delta)){event.preventDefault();event.stopPropagation();}});
  container.addEventListener('wheel',event=>{if(event.target.closest('.native-street-view'))return;if(getSpot()?.provider!=='google-street-view'||isDisabled())return;event.preventDefault();event.stopPropagation();change({fov:event.deltaY>0?10:-10});},{passive:false});
  // WebKit must know before scrolling starts that a loaded local panorama owns this touch.
  const ownsTouch=event=>isLive()&&!isDisabled()&&!event.target.closest('button,.native-street-view');
  container.addEventListener('touchstart',event=>{if(ownsTouch(event)){event.preventDefault();event.stopPropagation();}},{passive:false});
  container.addEventListener('touchmove',event=>{if(drag&&ownsTouch(event)){event.preventDefault();event.stopPropagation();}},{passive:false});
  container.addEventListener('dragstart',event=>event.preventDefault());
  container.addEventListener('pointerdown',event=>{if(event.button!==0||event.target.closest('button,.native-street-view')||getSpot()?.provider!=='google-street-view'||isDisabled())return;if(event.pointerType!=='touch')container.focus({preventScroll:true});drag={id:event.pointerId,x:event.clientX,y:event.clientY,start:view(getSpot()),width:container.getBoundingClientRect().width||300,moved:false};suppressClick=false;container.setPointerCapture(event.pointerId);event.preventDefault();event.stopPropagation();});
  container.addEventListener('pointermove',event=>{if(!drag||event.pointerId!==drag.id)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.hypot(dx,dy)<4&&!drag.moved)return;drag.moved=true;onChange(adjust(drag.start,{heading:-dx*drag.start.fov/drag.width,pitch:dy*drag.start.fov/drag.width},false));container.classList.add('street-view-dragging');hint.textContent='Updating view…';refresh();preview();event.preventDefault();event.stopPropagation();});
  function finish(event){if(!drag||drag.id!==event.pointerId)return;const moved=drag.moved;drag=null;container.classList.remove('street-view-dragging');hint.textContent='Drag to look · + / − to zoom';if(moved){suppressClick=true;onChange(adjust(view(getSpot()),{}));refresh();commit();}if(container.hasPointerCapture(event.pointerId))container.releasePointerCapture(event.pointerId);}
  container.addEventListener('pointerup',finish);container.addEventListener('pointercancel',finish);
  container.addEventListener('click',event=>{if(suppressClick){suppressClick=false;event.preventDefault();event.stopImmediatePropagation();}},{capture:true});
  function reset(){clearTimeout(timer);timer=null;lastPreview=0;drag=null;suppressClick=false;container.classList.remove('street-view-dragging');hint.textContent='Drag to look · + / − to zoom';refresh();}
  refresh();return {refresh,reset,dispose(){clearTimeout(timer);drag=null;}};
 }
 global.PhotoScoutStreetView={view,adjust,keyDelta,bind};
})(window);
