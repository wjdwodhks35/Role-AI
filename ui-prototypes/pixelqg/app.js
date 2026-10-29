"use strict";
// Static, deterministic demo. No network requests or privileged actions.
const initialAgents = Object.freeze([
  {id:"assistant",name:"Assistant",symbol:"◆",color:"#8cf3c4",room:"research",status:"running",task:"요청 분석 및 관련 기억 검색",model:"qwen2.5:7b",input:14280,output:1930,seconds:185},
  {id:"developer",name:"Developer",symbol:"▣",color:"#8bd2ff",room:"code",status:"running",task:"토큰 예산 관리자 설계",model:"qwen3:4b",input:26810,output:4120,seconds:402},
  {id:"tester",name:"Test Worker",symbol:"▥",color:"#ffd38a",room:"test",status:"testing",task:"압축률 및 복원 해시 검증",model:"스크립트 / pytest",input:0,output:0,seconds:78},
  {id:"reviewer",name:"Reviewer",symbol:"◇",color:"#e3b4ff",room:"rest",status:"done",task:"변경 코드 리뷰 완료",model:"qwen2.5:7b",input:8520,output:1090,seconds:122},
  {id:"collector",name:"Collector",symbol:"▤",color:"#ffa8a8",room:"rest",status:"idle",task:"대기 중",model:"로컬 작업",input:0,output:0,seconds:0}
]);
const activity = [
  {time:"10:24",text:"Test Worker · 압축 데이터 복원 검증 시작"},
  {time:"10:21",text:"Developer · 토큰 예산 관리 모듈 작업 중"},
  {time:"10:18",text:"Reviewer · 코드 리뷰 완료"},
  {time:"10:12",text:"Assistant · 문맥 검색 작업 시작"},
  {time:"10:08",text:"Collector · 대기 상태 전환"}
];
const statusLabel={running:"실행 중",testing:"테스트",idle:"대기",done:"완료"};
let selectedId="developer";
const byId=id=>document.getElementById(id);
const format=n=>new Intl.NumberFormat("ko-KR").format(n);
function node(tag,className,text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;}
function selectAgent(id){selectedId=id;render();}
function renderMetrics(){
  byId("metric-total").textContent=format(initialAgents.length);
  byId("metric-active").textContent=format(initialAgents.filter(a=>a.status==="running"||a.status==="testing").length);
  byId("metric-tokens").textContent=format(initialAgents.reduce((n,a)=>n+a.input+a.output,0));
  byId("metric-done").textContent=format(initialAgents.filter(a=>a.status==="done").length);
}
function renderOffice(){
  for(const room of document.querySelectorAll("[data-room]"))room.replaceChildren();
  for(const agent of initialAgents){
    const button=node("button","sprite");
    button.type="button";button.setAttribute("aria-label",agent.name+" 상세 정보");
    button.setAttribute("aria-pressed",String(selectedId===agent.id));
    const face=node("span","pixel-face",agent.symbol);face.style.color=agent.color;
    button.append(face,node("span","sprite-name",agent.name),node("span","sprite-state",statusLabel[agent.status]));
    button.addEventListener("click",()=>selectAgent(agent.id));
    document.querySelector('[data-room="'+agent.room+'"]').append(button);
  }
}
function renderDetail(){
  const agent=initialAgents.find(a=>a.id===selectedId)||initialAgents[0];
  byId("detail-avatar").textContent=agent.symbol;byId("detail-avatar").style.color=agent.color;
  byId("detail-name").textContent=agent.name;
  byId("detail-status").textContent=statusLabel[agent.status];
  byId("detail-status").className="status "+agent.status;
  byId("detail-task").textContent=agent.task;
  byId("detail-model").textContent=agent.model;
  byId("detail-input").textContent=format(agent.input);
  byId("detail-output").textContent=format(agent.output);
  byId("detail-duration").textContent=agent.seconds+"초 (샘플)";
}
function renderAgents(){
  const filter=byId("status-filter").value;
  const list=byId("agent-list");list.replaceChildren();
  const agents=initialAgents.filter(a=>filter==="all"||a.status===filter);
  if(!agents.length){list.append(node("p","empty-state","해당 상태의 Agent가 없습니다."));return;}
  for(const agent of agents){
    const button=node("button","agent-row");button.type="button";
    button.setAttribute("aria-pressed",String(agent.id===selectedId));
    const avatar=node("span","avatar",agent.symbol);avatar.style.color=agent.color;
    const desc=node("span","agent-description");desc.append(node("strong","",agent.name),node("small","",agent.task));
    button.append(avatar,desc,node("span","row-state",statusLabel[agent.status]));
    button.addEventListener("click",()=>selectAgent(agent.id));list.append(button);
  }
}
function renderActivity(){
  const list=byId("activity-list");list.replaceChildren();
  for(const item of activity){const li=node("li");li.append(node("time","",item.time),node("span","",item.text));list.append(li);}
}
function render(){renderMetrics();renderOffice();renderDetail();renderAgents();renderActivity();}
byId("status-filter").addEventListener("change",renderAgents);
byId("reset-demo").addEventListener("click",()=>{selectedId="developer";byId("status-filter").value="all";render();});
render();