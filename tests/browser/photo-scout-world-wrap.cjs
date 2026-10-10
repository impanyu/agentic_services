// Run with NODE_PATH pointing to a Playwright installation.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'../..');
const initialization=readFileSync(path.join(root,'photo-scout-site/app.js'),'utf8').split('L.control.zoom(')[0];
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 try{
  for(const viewport of [{width:1200,height:800},{width:390,height:844}]){
   for(const direction of ['east','west']){
    const page=await browser.newPage({viewport});
    try{
     await page.setContent('<style>html,body{margin:0}#map{width:100vw;height:100vh}</style><div id="map"></div>');
     await page.addStyleTag({path:path.join(root,'photo-scout-site/leaflet.css')});
     await page.addScriptTag({path:path.join(root,'photo-scout-site/leaflet.js')});
     await page.evaluate(initialization+';window.testMap=map;');
     await page.evaluate(direction=>{
      const map=window.testMap;map.options.inertia=false;map.options.zoomAnimation=false;
      const origin=direction==='east'?[35,104]:[35,-88];
      map.setView(origin,3);
      window.savedPins=[
       L.marker([30.66,104.08],{icon:L.divIcon({className:'saved-camera',html:'Chengdu',iconSize:[60,24]})}).addTo(map),
       L.marker([41.88,-87.62],{icon:L.divIcon({className:'saved-dot',html:'Chicago',iconSize:[60,24]})}).addTo(map)
      ];
     },direction);
     // Cross the date line by dragging only, never by selecting a history item.
     const displacement=950,step=Math.min(viewport.width-90,displacement);
     let remaining=displacement;
     while(remaining>0){
      const distance=Math.min(step,remaining),start=direction==='east'?viewport.width-45:45,end=start+(direction==='east'?-distance:distance);
      await page.mouse.move(start,viewport.height/2);await page.mouse.down();await page.mouse.move(end,viewport.height/2,{steps:20});await page.mouse.up();
      remaining-=distance;
     }
     const status=await page.evaluate(direction=>{
      const map=window.testMap,pin=window.savedPins[direction==='east'?1:0],point=map.latLngToContainerPoint(pin.getLatLng()),size=map.getSize();
      return {center:map.getCenter().lng,inViewport:point.x>=0&&point.x<=size.x&&point.y>=0&&point.y<=size.y,
       connected:pin.getElement().isConnected,count:window.savedPins.length};
     },direction);
     assert.ok(status.inViewport,JSON.stringify({direction,viewport,status}));
     assert.ok(status.connected);assert.equal(status.count,2);assert.ok(Math.abs(status.center)<=180);
     // Zoom in on the destination and confirm markers stay positioned.
     await page.evaluate(direction=>{const map=window.testMap,pin=window.savedPins[direction==='east'?1:0];map.setZoomAround(map.latLngToContainerPoint(pin.getLatLng()),5);},direction);
     assert.ok(await page.evaluate(direction=>window.testMap.getBounds().contains(window.savedPins[direction==='east'?1:0].getLatLng()),direction));
     console.log('PASS',viewport.width,direction);
    }finally{await page.close();}
   }
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
