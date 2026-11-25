// The page drawn without a browser: runs dist/live.js against a snapshot (GET /api/snapshot) with a minimal DOM
// stand-in and draws every view; exits 1 if one throws. node check_page.js dist/live.js <snapshot.json>
const fs=require('fs');const [,,jsPath,snapPath]=process.argv;
const el=()=>new Proxy({style:{},dataset:{},classList:{add(){},remove(){},toggle(){}},addEventListener(){},focus(){},blur(){},setSelectionRange(){},querySelector:()=>el(),querySelectorAll:()=>[],closest:()=>null,textContent:'',innerHTML:'',value:''},{get:(t,k)=>k in t?t[k]:undefined,set:(t,k,v)=>{t[k]=v;return true}});
const app=el();const nodes={'#app':app};
global.document={querySelector:s=>nodes[s]||(nodes[s]=el()),querySelectorAll:()=>[],createElement:()=>el(),activeElement:null};
global.window={location:{hash:''},scrollTo(){},scrollY:0,getSelection:()=>'',onhashchange:null};global.location=window.location;
global.setInterval=()=>0;global.AbortSignal={timeout:()=>undefined};global.confirm=()=>false;
const snapText=fs.readFileSync(snapPath,'utf8');
global.fetch=async()=>({ok:true,status:200,text:async()=>snapText,json:async()=>JSON.parse(snapText)});
let code=fs.readFileSync(jsPath,'utf8');
code+="\n;globalThis.__render=render;globalThis.__views=views;globalThis.__setSnap=t=>{snapshot=JSON.parse(t)};";
try{eval(code)}catch(e){console.log('LOAD ERROR',e.stack.split('\n').slice(0,3).join(' | '));process.exit(1)}
setTimeout(()=>{__setSnap(snapText);let bad=0;for(const [v] of __views){window.location.hash='#'+v;try{__render();const h=app.innerHTML;console.log(v.padEnd(12),'ok',h.length,'chars',/Data is unavailable/.test(h)?'UNAVAILABLE':'')}catch(e){bad++;console.log(v.padEnd(12),'ERROR',e.message)}}process.exit(bad?1:0)},200);
