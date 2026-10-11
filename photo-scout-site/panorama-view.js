// Reuse one full sphere; camera changes render on the GPU without network calls.
(function(global){
 const states=new WeakMap(),sources=new Map();
 const vertex='attribute vec2 a; varying vec2 uv; void main(){uv=a*.5+.5;gl_Position=vec4(a,0.,1.);}';
 const fragment=`precision highp float; varying vec2 uv; uniform sampler2D sphere;
 uniform vec3 forward,right,up,pForward,pRight,pUp; uniform float tangent,aspect;
 void main(){vec2 xy=uv*2.-1.;vec3 ray=normalize(forward+xy.x*tangent*right+xy.y*tangent*aspect*up);
 float longitude=atan(dot(ray,pRight),dot(ray,pForward));float latitude=asin(clamp(dot(ray,pUp),-1.,1.));
 gl_FragColor=texture2D(sphere,vec2(fract(longitude/6.28318530718+.5),clamp(.5-latitude/3.14159265359,0.,1.)));}`;
 function basis(heading,pitch,roll=0){const h=heading*Math.PI/180,p=pitch*Math.PI/180,r=roll*Math.PI/180;
 const f=[Math.sin(h)*Math.cos(p),Math.sin(p),Math.cos(h)*Math.cos(p)],right=[Math.cos(h),0,-Math.sin(h)],up=[-Math.sin(h)*Math.sin(p),Math.cos(p),-Math.cos(h)*Math.sin(p)];
 return {forward:f,right:right.map((v,i)=>v*Math.cos(r)+up[i]*Math.sin(r)),up:up.map((v,i)=>v*Math.cos(r)-right[i]*Math.sin(r))};}
 function shader(gl,kind,source){const s=gl.createShader(kind);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error('Panorama shader unavailable');return s;}
 async function source(pano,load){let entry=sources.get(pano);if(entry&&(entry.expires==null||entry.expires>Date.now()/1000))return entry.promise;
 entry={expires:null};entry.promise=Promise.resolve().then(load).then(async data=>{const image=new Image();image.src=data.image;await image.decode();entry.expires=data.expiresAt||0;return {image,metadata:data};}).catch(error=>{if(sources.get(pano)===entry)sources.delete(pano);throw error;});sources.set(pano,entry);
 while(sources.size>12)sources.delete(sources.keys().next().value);return entry.promise;}
 function draw(state){state.frame=null;if(!state.ready||!state.surface.isConnected)return;
 const {canvas,surface,gl,program}=state,rect=surface.getBoundingClientRect(),ratio=Math.min(global.devicePixelRatio||1,2);
 const width=Math.max(1,Math.round(rect.width*ratio)),height=Math.max(1,Math.round(rect.height*ratio));if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}
 const v=global.PhotoScoutStreetView.view(state.getSpot()),b=basis(v.heading,v.pitch);gl.viewport(0,0,width,height);gl.useProgram(program);
 for(const [name,value] of Object.entries(b))gl.uniform3fv(gl.getUniformLocation(program,name),value);
 gl.uniform1f(gl.getUniformLocation(program,'tangent'),Math.tan(v.fov*Math.PI/360));gl.uniform1f(gl.getUniformLocation(program,'aspect'),height/width);gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
 }
 function refresh(surface){const state=states.get(surface);if(!state?.ready)return false;const pano=new URL(state.getSpot().sourceUrl).searchParams.get('pano');if(pano!==state.pano){detach(surface);return false;}
 if(state.frame==null)state.frame=global.requestAnimationFrame(()=>draw(state));return true;}
 function detach(surface){const state=states.get(surface);if(!state)return;state.cancelled=true;if(state.frame!=null)global.cancelAnimationFrame(state.frame);state.resize?.disconnect();state.canvas.remove();state.credit?.remove();state.gl.deleteTexture(state.texture);state.gl.deleteBuffer(state.buffer);state.gl.deleteProgram(state.program);surface.classList.remove('local-panorama-active');states.delete(surface);}
 async function attach(surface,{getSpot,loadPanorama}){if(getSpot()?.provider!=='google-street-view')return false;
 const pano=new URL(getSpot().sourceUrl).searchParams.get('pano'),old=states.get(surface);if(old?.pano===pano)return old.loading;if(old)detach(surface);
 const canvas=document.createElement('canvas'),gl=canvas.getContext('webgl',{alpha:false,antialias:false,preserveDrawingBuffer:false});if(!gl)return false;
 canvas.className='local-panorama';canvas.setAttribute('aria-hidden','true');
 const state={surface,canvas,gl,getSpot,pano,ready:false,cancelled:false,frame:null};states.set(surface,state);
 state.loading=(async()=>{try{const {image,metadata}=await source(pano,loadPanorama);if(state.cancelled||!surface.isConnected||new URL(getSpot().sourceUrl).searchParams.get('pano')!==pano){if(states.get(surface)===state)detach(surface);return false;}
 const program=gl.createProgram();state.program=program;const shaders=[shader(gl,gl.VERTEX_SHADER,vertex),shader(gl,gl.FRAGMENT_SHADER,fragment)];for(const s of shaders)gl.attachShader(program,s);gl.linkProgram(program);for(const s of shaders)gl.deleteShader(s);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error('Panorama renderer unavailable');gl.useProgram(program);
 const buffer=gl.createBuffer();state.buffer=buffer;gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);const a=gl.getAttribLocation(program,'a');gl.enableVertexAttribArray(a);gl.vertexAttribPointer(a,2,gl.FLOAT,false,0,0);
 const texture=gl.createTexture();state.texture=texture;gl.bindTexture(gl.TEXTURE_2D,texture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,image);
 const b=basis(metadata.heading||0,90-(metadata.tilt??90),metadata.roll||0);for(const [name,value] of Object.entries(b))gl.uniform3fv(gl.getUniformLocation(program,'p'+name[0].toUpperCase()+name.slice(1)),value);
 const credit=document.createElement('span');credit.className='local-panorama-credit';credit.textContent='Google · '+(metadata.copyright||'Google');state.credit=credit;surface.append(canvas,credit);state.ready=true;surface.classList.add('local-panorama-active');draw(state);
 if(global.ResizeObserver){state.resize=new ResizeObserver(()=>refresh(surface));state.resize.observe(surface);}canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();if(states.get(surface)===state)detach(surface);});return true;
 }catch{if(states.get(surface)===state)detach(surface);return false;}})();return state.loading;
 }
 global.PhotoScoutLocalPanorama={attach,refresh,detach,isActive:surface=>Boolean(states.get(surface)?.ready),basis};
})(window);
