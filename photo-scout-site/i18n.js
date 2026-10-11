/* Presentation-only localization for the imperative UI, including async panels.
 * Catalog keys are stable English UI messages. Unknown text is left untouched.
 * Never modify values, names, URLs, API payloads or persisted records. */
(() => {
 'use strict';
 function resolveLocale(languages){
  const first=Array.from(languages||[]).find(value=>typeof value==='string'&&value.trim());
  if(!first||!/^zh(?:-|_|$)/i.test(first))return 'en';
  if(/(?:^|-|_)hans(?:-|_|$)/i.test(first))return 'zh-Hans';
  return /(?:hant|tw|hk|mo)(?:-|_|$)/i.test(first)?'zh-Hant':'zh-Hans';
 }
 const locale=resolveLocale(navigator.languages?.length?navigator.languages:[navigator.language]);
 const dictionary=window.PhotoScoutMessages?.[locale]||{};
 const own=key=>Object.prototype.hasOwnProperty.call(dictionary,key);
 function t(value){
  if(typeof value!=='string'||locale==='en')return value;
  const core=value.trim();
  if(own(core))return value.replace(core,dictionary[core]);
  // Preserve the leading UI icon and trailing counters.
  const icon=core.match(/^([◷▧◉⌖✓☷♡♥☆★📷✦]\s*)(.+)$/u);
  if(icon){const translated=t(icon[2]);if(translated!==icon[2])return value.replace(core,icon[1]+translated);}
  const rules=[
   [/^Within ([\d.]+) (m|km)$/,(n,u)=>`${n} ${u==='km'?'公里':'米'}以内`],
   [/^Searching within ([\d.]+) (m|km)$/,(n,u)=>`搜索半径 ${n} ${u==='km'?'公里':'米'}`],
   [/^Facing (N|NE|E|SE|S|SW|W|NW) · (\d+)°$/,(d,n)=>`朝向 ${{N:'北',NE:'东北',E:'东',SE:'东南',S:'南',SW:'西南',W:'西',NW:'西北'}[d]} · ${n}°`],
   [/^Found (\d+) nearby places\. Preparing their photos…$/,(n)=>`找到 ${n} 个附近地点，正在准备图片…`],
   [/^(\d+) of (\d+) characters$/,(n,total)=>`显示 ${n} / ${total} 个角色`],
   [/^Checking (.+)…$/,(name)=>`正在检查 ${name}…`],
   [/^Preparing (.+)…$/,(name)=>`正在准备 ${name}…`],
   [/^Your current location is selected( · estimated accuracy ±\d+ m)?\.$/,(accuracy)=>`已选择当前位置${accuracy?accuracy.replace('estimated accuracy','预计精度'):''}。`],
   [/^Searching for: (.+)$/,(intent)=>`${dictionary['Searching for:']}${intent}`],
   [/^Image source: (.+)$/,(source)=>`${dictionary['Image source:']}${source}`],
   [/^Photo idea: (.+)$/,(idea)=>`${dictionary['Photo idea:']}${idea}`],
   [/^Uncertainty: (.+)$/,(note)=>`${dictionary['Uncertainty:']}${note}`],
   [/^Inspected camera direction: ([\d.]+)°$/,(n)=>`已评估视角：${n}°`],
   [/^(\d+)\/100 subjective photo score · (.+) confidence$/,(n,c)=>`${n}/100 拍照评分 · ${c} 置信度`],
  ];
  for(const [pattern,format] of rules){const match=core.match(pattern);if(match){let output=format(...match.slice(1));if(locale==='zh-Hant')output=traditional(output);return value.replace(core,output);}}
  // Metadata has explicit separators. Translate only complete known segments.
  if(core.includes(' · ')){const output=core.split(' · ').map(part=>t(part)).join(' · ');if(output!==core)return value.replace(core,output);}
  return value;
 }
 // Only characters used by the parameterized templates; full messages live in the catalog.
 function traditional(text){const replacements={'以内':'以內','搜索':'搜尋','公里':'公里','朝向':'朝向','东北':'東北','东南':'東南','东':'東','显示':'顯示','个':'個','附近地点':'附近地點','正在准备':'正在準備','图片':'圖片','检查':'檢查','已选择当前位置':'已選擇目前位置','预计精度':'預計精度','已评估视角':'已評估視角','评分':'評分','置信度':'信賴度'};for(const [from,to] of Object.entries(replacements))text=text.split(from).join(to);return text;}
 const textSources=new WeakMap(),attributeSources=new WeakMap();
 const excluded='script,style,pre,code,textarea,[data-i18n-ignore],.leaflet-control-attribution,.GMP-attribution,.comment-list,.shortlist-place-focus,.history-copy strong,.photo-history-copy strong,.photo-popup>strong,.avatar-pick,.studio-place,.saved-photo-place';
 function source(node){const record=textSources.get(node?.firstChild);return record&&node.textContent===record.output?record.input:node?.textContent;}
 function translateText(node){
  if(!node.parentElement||node.parentElement.closest(excluded))return;
  const record=textSources.get(node);if(record&&node.nodeValue===record.output)return;
  const input=node.nodeValue;if(node.parentElement.id==='account-name'&&input!=='Guest session')return;const output=t(input);if(output!==input){textSources.set(node,{input,output});node.nodeValue=output;}
 }
 const attributes=['placeholder','title','aria-label','alt'];
 function translateAttributes(element){
  if(element.closest('[data-i18n-ignore],.comment-list,.leaflet-control-attribution,.GMP-attribution'))return;
  for(const name of attributes){const input=element.getAttribute(name);if(!input)continue;const records=attributeSources.get(element)||{};if(records[name]?.output===input)continue;const output=t(input);if(output!==input){records[name]={input,output};attributeSources.set(element,records);element.setAttribute(name,output);}}
 }
 function localize(root){
  if(locale==='en'||!root)return;
  if(root.nodeType===3){translateText(root);return;}
  if(root.nodeType!==1)return;
  translateAttributes(root);
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);
  let node;while((node=walker.nextNode())){if(node.nodeType===3)translateText(node);else translateAttributes(node);}
 }
 window.PhotoScoutI18n={locale,t,resolveLocale,localize,source};
 document.documentElement.lang=locale;
 function start(){
  localize(document.body);
  if(locale==='en')return;
  // Observe only changed subtrees: no polling or rewriting the full map on each update.
  const observer=new MutationObserver(records=>{for(const record of records){if(record.type==='childList')record.addedNodes.forEach(localize);else if(record.type==='characterData')translateText(record.target);else translateAttributes(record.target);}});
  observer.observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:attributes});
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
