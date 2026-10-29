"use strict";
// Isolated static demo: no API calls, filesystem actions, or actual approvals.
const agents=[
{id:"assistant",name:"비서 Agent",glyph:"✿",color:"#ff9dce",x:27,y:35,role:"문서 정리 / 일정 관리",model:"qwen2.5:7b",state:"running",task:"관련 문서 5개 정리",description:"요청에 필요한 프로젝트 문서를 정리하고 관련 근거를 검색합니다.",tools:["문서 검색","RAG","메모리"],stage:"1. 분석",progress:55,time:"00:12:45",input:4200,output:950,cache:420,resources:[26,40,39]},
{id:"developer",name:"개발 Agent",glyph:"◆",color:"#49e7b0",x:49,y:31,role:"코드 구현 및 수정",model:"qwen2.5-coder:7b (예시)",state:"running",task:"API 캐싱 로직 구현 중",description:"Redis 기반 캐싱 로직을 구현하고 있습니다. 기존 DB 조회 로직을 분석하여 캐시 키 구조를 설계 중입니다.",tools:["VS Code","Python","Redis","Git","Terminal"],stage:"2. 수정",progress:70,time:"00:42:17",input:12430,output:8912,cache:4320,resources:[38,62,54]},
{id:"tester",name:"테스트 Agent",glyph:"▣",color:"#ffc36e",x:77,y:37,role:"자동 테스트 / 성능 측정",model:"pytest / 로컬 모델",state:"testing",task:"부하 테스트 시나리오 실행",description:"API 응답 시간과 회귀 여부를 확인하기 위한 테스트 시나리오를 검증합니다.",tools:["pytest","Python","성능 테스트"],stage:"3. 테스트",progress:40,time:"00:20:05",input:2200,output:880,cache:0,resources:[45,33,42]},
{id:"reviewer",name:"검토 Agent",glyph:"◇",color:"#cb9eff",x:28,y:74,role:"코드 리뷰 / 보안 검토",model:"qwen2.5:7b",state:"idle",task:"보안 취약점 검토 대기",description:"수정 코드가 기존 보안 정책 및 요구사항을 위반하지 않는지 검토할 예정입니다.",tools:["코드 비교","테스트 결과"],stage:"4. 검토",progress:0,time:"00:00:00",input:1100,output:320,cache:0,resources:[9,0,16]},
{id:"learner",name:"학습 Agent",glyph:"✦",color:"#f0a1ef",x:76,y:76,role:"데이터 학습 / 모델 평가",model:"로컬 모델",state:"idle",task:"검증된 데이터 대기",description:"학습은 자동 실행되지 않습니다. 데이터 검수와 사용자 승인이 필요합니다.",tools:["데이터 검수","평가"],stage:"대기",progress:0,time:"00:00:00",input:0,output:0,cache:0,resources:[0,0,0]}
];
const logs=[
{time:"15:28:14",role:"개발",kind:"normal",text:"API 응답 시간 분석 완료",agent:"developer"},
{time:"15:27:50",role:"테스트",kind:"test",text:"단위 테스트 실행 중 (8/12)",agent:"tester"},
{time:"15:27:11",role:"분석",kind:"normal",text:"병목 지점 3개 발견",agent:"assistant"},
{time:"15:26:33",role:"시스템",kind:"normal",text:"새로운 작업이 추가되었습니다.",agent:null},
{time:"15:25:18",role:"검토",kind:"review",text:"코드 가이드라인 검토 대기",agent:"reviewer"},
{time:"15:24:05",role:"테스트",kind:"error",text:"데모 오류 이벤트: 응답 지연 감지",agent:"tester"}
];
const approvals=[
{id:"a1",title:"코드 수정안 적용 요청",detail:"API 응답 시간 개선 (예상 2.4s → 1.8s)"},
{id:"a2",title:"데이터 구조 변경 제안",detail:"저장 공간 절약을 위한 구조 검토"}
];
const $=id=>document.getElementById(id);
const fmt=n=>new Intl.NumberFormat("ko-KR").format(n);
function el(tag,cls,value){const node=document.createElement(tag);if(cls)node.className=cls;if(value!==undefined)node.textContent=value;return node;}
let selected="developer",tab="work",logFilter="all";
const decisions=new Map();
function renderStages(){
const stages=[["분석","로그/코드 분석 중"],["수정","개선안 작성 대기"],["테스트","자동 테스트 대기"],["검토","결과 확인 대기"]];
const root=$("stages");root.replaceChildren();
stages.forEach(([name,detail],i)=>{const s=el("div","stage "+(i===0?"current":"" ));s.append(el("span","stage-number",String(i+1)));const c=el("span","stage-copy");c.append(el("strong","",name),el("small","",detail));s.append(c);root.append(s);});
}
function renderOffice(){
const root=$("office-avatars");root.replaceChildren();
for(const a of agents){
const b=el("button","agent-sprite");b.type="button";b.style.left=a.x+"%";b.style.top=a.y+"%";b.style.setProperty("--agent-color",a.color);b.setAttribute("aria-label",a.name+" 상세 보기");b.setAttribute("aria-pressed",String(a.id===selected));
b.append(el("span","agent-label",a.name),el("span","agent-character",a.glyph),el("span","agent-state",stateText(a.state)));b.addEventListener("click",()=>{selected=a.id;renderOffice();renderDetail();});root.append(b);
}}
function stateText(state){return {running:"작업 중",testing:"테스트 중",idle:"대기",failed:"오류"}[state]||"대기";}
function renderDetail(){
const a=agents.find(x=>x.id===selected)||agents[0];
$("profile-pixel").textContent=a.glyph;$("profile-pixel").style.color=a.color;
$("detail-name").textContent=a.name;$("detail-state").textContent=stateText(a.state);$("detail-state").className="state "+a.state;
for(const [id,value] of Object.entries({"detail-model":a.model,"detail-role":a.role,"detail-task":a.task,"detail-time":a.time,"detail-stage-label":a.stage+" · "+a.progress+"%","detail-description":a.description,"detail-input":fmt(a.input),"detail-output":fmt(a.output),"detail-cache":fmt(a.cache),"detail-total":fmt(a.input+a.output+a.cache)}))$(id).textContent=value;
$("detail-stage-bar").style.width=a.progress+"%";
const tools=$("detail-tools");tools.replaceChildren();a.tools.forEach(t=>tools.append(el("span","tool",t)));
const resources=$("resource-bars");resources.replaceChildren();["CPU","GPU","메모리"].forEach((name,i)=>{const row=el("div","resource-row");const track=el("div","resource-track");const fill=el("div");fill.style.width=a.resources[i]+"%";track.append(fill);row.append(el("span","",name),track,el("span","",a.resources[i]+"%"));resources.append(row);});
const history=$("detail-activity");history.replaceChildren();const related=logs.filter(l=>l.agent===a.id);if(!related.length)history.append(el("li","","관련 샘플 로그가 없습니다."));else related.forEach(l=>history.append(el("li","",l.time+" · "+l.text)));
for(const button of document.querySelectorAll("[data-tab]"))button.setAttribute("aria-pressed",String(button.dataset.tab===tab));
for(const name of ["work","metrics","activity"])$("tab-"+name).hidden=name!==tab;
}
function renderBottom(){
const phases=[["분석",100,"#51c8f7"],["수정",60,"#b4a3e9"],["테스트",20,"#f3b15c"],["검토",0,"#758cae"]];const root=$("phase-bars");root.replaceChildren();
phases.forEach(([name,pct,color])=>{const row=el("div","phase-row");const track=el("div","phase-track");const fill=el("div");fill.style.width=pct+"%";fill.style.setProperty("--phase-color",color);track.append(fill);row.append(el("span","",name),track,el("span","",pct+"%"));root.append(row);});
$("metric-tokens").textContent=fmt(agents.reduce((n,a)=>n+a.input+a.output+a.cache,0));
}
function renderLogs(){
const root=$("log-list");root.replaceChildren();for(const log of logs.filter(l=>logFilter==="all"||l.kind==="error")){const li=el("li");li.append(el("time","",log.time),el("span","log-tag "+log.kind,"["+log.role+"]"),el("span","",log.text));root.append(li);}
for(const b of document.querySelectorAll("[data-log]"))b.setAttribute("aria-pressed",String(b.dataset.log===logFilter));
}
function renderApprovals(){
const root=$("approval-list");root.replaceChildren();$("approval-count").textContent=String(approvals.filter(a=>!decisions.has(a.id)).length);
for(const item of approvals){const row=el("div","approval-item");const info=el("div");info.append(el("strong","",item.title),el("p","",item.detail));const actions=el("div","approval-actions");
if(decisions.has(item.id)){actions.append(el("span","approval-result",decisions.get(item.id)+" (데모)"));}
else for(const [label,result] of [["승인","승인됨"],["거부","거부됨"]]){const b=el("button","",label);b.type="button";b.title="샘플 UI 상태만 변경합니다";b.addEventListener("click",()=>{decisions.set(item.id,result);renderApprovals();});actions.append(b);}
row.append(info,actions);root.append(row);}
}
document.querySelectorAll("[data-tab]").forEach(b=>b.addEventListener("click",()=>{tab=b.dataset.tab;renderDetail();}));
document.querySelectorAll("[data-log]").forEach(b=>b.addEventListener("click",()=>{logFilter=b.dataset.log;renderLogs();}));
$("detail-reset").addEventListener("click",()=>{selected="developer";tab="work";renderOffice();renderDetail();});
renderStages();renderOffice();renderDetail();renderBottom();renderLogs();renderApprovals();
