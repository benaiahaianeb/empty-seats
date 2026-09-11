// Label-consistency audit (owner invariant): drives the page in jsdom across
// origins, months and average mode; every schedule label must agree with the
// banks and day pattern beside it. npm i jsdom once, then: node tools/audit.js
// A second arg audits another built page, e.g. a per-airline check build.
const {JSDOM,VirtualConsole}=require("jsdom"), fs=require("fs");
const vc=new VirtualConsole(); vc.on("jsdomError",e=>console.log("JSDOM ERROR:",e.message));
const dom=new JSDOM(fs.readFileSync(require("path").join(__dirname,"..",process.argv[2]||"index.html"),"utf8"),
  {runScripts:"dangerously", pretendToBeVisual:true, virtualConsole:vc, url:"https://x/"});
const W=dom.window, D=W.document;
const setOrigin=code=>{
  const inp=D.getElementById("originInput");
  inp.value=code; inp.dispatchEvent(new W.Event("input",{bubbles:true}));
  const opt=[...D.getElementById("originMenu").children].find(o=>o.textContent.includes(code));
  if(!opt) return false;
  opt.dispatchEvent(new W.MouseEvent("mousedown",{bubbles:true}));
  return true;
};
const DAYS=["MO","TU","WE","TH","FR","SA","SU"];
let checked=0, bad=[];
function audit(tag){
  for(const el of D.getElementById("rows").children){
    const s=el.querySelector(".sched"); if(!s) continue;
    const label=(s.querySelector("b")||{textContent:""}).textContent.trim();
    const sub=[...s.querySelectorAll("small:not(.arrline)")].map(x=>x.textContent.trim()).join(" — ");
    const full=(label+" — "+sub).trim(); checked++;
    const times=(sub.match(/\b\d{1,2}:\d{2}\b/g)||[]).length;
    const dayTok=(sub.match(/\b(MO|TU|WE|TH|FR|SA|SU)\b/g)||[]).length;
    const exceptTok=(label.match(/\b(MO|TU|WE|TH|FR|SA|SU)\b/g)||[]).length;
    const pd=label.match(/^(\d+)×\/day$/i), pw=label.match(/^(\d+)×\/week$/i);
    // invariant 1: ≥2 visible banks ⇒ N×/day with N = bank count
    if(times>=2 && !pd) bad.push([tag,"≥2 banks not labelled ×/day",full]);
    if(times>=2 && pd && +pd[1]!==times) bad.push([tag,"×/day ≠ bank count",full]);
    // invariant 2: day-list pattern ⇒ N×/week with N = day count
    if(pw && dayTok && +pw[1]!==dayTok) bad.push([tag,"×/week ≠ day count",full]);
    // invariant 3: no bare "Daily" alongside a partial day pattern
    if(/^daily$/i.test(label) && dayTok>0 && dayTok<7) bad.push([tag,"bare Daily with day pattern",full]);
  }
}
setTimeout(()=>{
  const origins=["BOS","ATL","DTW","MSP","JFK","LAX","SLC","SEA","LGA","RDU"];
  for(const o of origins){
    if(!setOrigin(o)){ console.log("skip origin",o); continue; }
    const months=[...D.getElementById("months").children];
    [0, 3, 6, 9].forEach(i=>{
      if(!months[i]) return;
      months[i].dispatchEvent(new W.MouseEvent("click",{bubbles:true}));
      audit(o+"/m"+i);
    });
    // 3-year average mode
    D.getElementById("spanAvg").dispatchEvent(new W.MouseEvent("click",{bubbles:true}));
    audit(o+"/avg");
    D.getElementById("spanLatest").dispatchEvent(new W.MouseEvent("click",{bubbles:true}));
  }
  console.log("schedule labels checked:", checked);
  console.log("violations:", bad.length);
  bad.slice(0,10).forEach(b=>console.log("   !", b[0], "|", b[1], "|", b[2]));
  console.log(bad.length? "FAIL":"PASS");
  process.exit(bad.length?1:0);
},3000);
