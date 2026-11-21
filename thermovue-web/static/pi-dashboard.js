'use strict';
const $=s=>document.querySelector(s);
const number=(v,d=1)=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v))?Number(v).toLocaleString('id-ID',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
const clock=t=>new Date(t*1000).toLocaleTimeString('id-ID',{timeZone:'Asia/Jakarta',hour12:false});
let lastState=null,lastReceived=0,lastNetwork=0;
async function get(path){const r=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error('HTTP '+r.status);return r.json()}
function renderState(s){
 const ageSinceFetch=(Date.now()-lastReceived)/1000;
 let connected=0;
 for(let i=1;i<=2;i++){
  const cfg=s.config?.['sensor'+i]||{}, el=$('#s'+i), raw=s['s'+i];
  const valid=typeof raw==='number'&&Number.isFinite(raw), t=Number(s.timestamps?.[i-1]||0);
  const fresh=valid&&s['s'+i+'_connected']&&t>0&&s.server_time-t+ageSinceFetch<=4;
  const value=valid?ThermoCalibration.apply(raw,cfg):null;
  const alarm=fresh&&((cfg.low_alarm!=null&&value<=cfg.low_alarm)||(cfg.high_alarm!=null&&value>=cfg.high_alarm));
  connected+=fresh?1:0;
  el.classList.toggle('stale',!fresh);el.classList.toggle('alarm',!!alarm);
  el.querySelector('strong').textContent=number(value);
  el.querySelector('b').textContent=fresh?(alarm?'● Alarm batas suhu':'● Terhubung'):'● Data tidak terkini';
  el.querySelector('.label span').textContent='Tipe K · ID '+(i===1?s.config?.modbus?.slave_id??1:s.config?.modbus?.sensor2_slave_id??5);
  el.querySelector('.sensor-foot small').textContent=fresh?'Batas '+number(cfg.low_alarm,0)+' – '+number(cfg.high_alarm,0)+' °C':t?'Terakhir '+clock(t):'Belum ada pembacaan';
 }
 $('#connection').textContent=connected===2?'● Kedua sensor aktif':connected?'● 1 sensor tidak terkini':'● Koneksi terputus';
 $('#connection').className='pill '+(connected===2?'ok':'bad');
 $('#updated').textContent='Data '+clock(s.timestamp||s.server_time)+' WIB';
 $('#archive-info').textContent='Arsip otomatis setiap '+(s.config?.logging?.interval_minutes??'—')+' menit per sensor.';
 drawChart(s);
 renderCalibration();
}
function drawChart(s){
 const end=s.server_time, start=end-300;
 const series=[0,1].map(i=>(s.history?.[i]||[]).filter(p=>p[0]>=start&&p[0]<=end&&typeof p[1]==='number'&&Number.isFinite(p[1])).map(p=>[p[0],ThermoCalibration.apply(p[1],s.config?.['sensor'+(i+1)])]));
 const values=series.flat().map(p=>p[1]);
 if(!values.length){$('#chart').innerHTML='<text x="360" y="55" text-anchor="middle">Belum ada data pada rentang ini</text>';return}
 let min=Math.min(...values), max=Math.max(...values), pad=Math.max(1,(max-min)*.12);min-=pad;max+=pad;
 const x=t=>44+(t-start)/300*666, y=v=>77-(v-min)/(max-min)*65;
 let svg=[min,(min+max)/2,max].map(v=>`<line x1="44" x2="710" y1="${y(v)}" y2="${y(v)}" stroke="#2a3d46"/><text x="0" y="${y(v)+3}">${number(v,0)}°</text>`).join('');
 series.forEach((points,i)=>{
  let d='',prev=null;
  for(const p of points){d+=(prev&&p[0]-prev[0]<=8?' L':' M')+x(p[0]).toFixed(2)+','+y(p[1]).toFixed(2);prev=p}
  svg+=`<path d="${d}" fill="none" stroke="${i?'#83bfff':'#51d7bd'}" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  if(points.length){const p=points[points.length-1];svg+=`<circle cx="${x(p[0])}" cy="${y(p[1])}" r="2.5" fill="${i?'#83bfff':'#51d7bd'}"/>`}
 });
 $('#chart').innerHTML=svg;
}
const metric=(label,value,detail)=>{const el=document.createElement('div');el.className='metric';for(const [tag,text]of [['small',label],['strong',value],['span',detail]]){const child=document.createElement(tag);child.textContent=text;el.append(child)}return el};
async function pollNetwork(){try{
 const data=await get('/api/network'), n=data.network||{},a=data.api||{};
 const age=data.server_time-Date.parse(a.sent_at_wib)/1000;
 const fresh=Number.isFinite(age)&&age<30;
 const pressure=$('#pressure'), solenoid=$('#solenoid');
 pressure.querySelector('strong').textContent=number(a.pressure_kpa_estimated,2);
 pressure.classList.toggle('stale',!fresh);
 pressure.querySelector('.sensor-foot small').textContent=fresh?'Kiriman '+clock(Date.parse(a.sent_at_wib)/1000):'Kiriman tidak terkini';
 solenoid.querySelector('strong').textContent=a.solenoid_on==='0'?'OFF':a.solenoid_on==='1'?'ON':'—';
 solenoid.classList.toggle('stale',!fresh);
 solenoid.classList.toggle('on',fresh&&a.solenoid_on==='1');
 solenoid.querySelector('.sensor-foot small').textContent=fresh?'Kiriman '+clock(Date.parse(a.sent_at_wib)/1000):'Kiriman tidak terkini';
 $('#api-summary').textContent='API · '+(fresh?'HTTP '+(a.http_status||'—')+' · '+number(a.response_time_ms,0)+' ms':'status belum terkini');
 $('#wifi-summary').textContent=(n.ssid||'Wi-Fi —')+' · '+number(n.rssi_dbm,0)+' dBm';
 $('#network-time').textContent=n.network_checked_at_wib?clock(Date.parse(n.network_checked_at_wib)/1000)+' WIB':'Belum ada pemeriksaan';
 $('#metrics').replaceChildren(
 metric('WI-FI',n.ssid||'—',n.ip_address||'IP —'),
 metric('SINYAL',number(n.rssi_dbm,0)+' dBm','Ping gateway '+number(n.ping_gateway_ms)+' ms'),
 metric('PENGIRIMAN API','HTTP '+(a.http_status||'—'),fresh?'Diterima '+(a.accepted||'0')+' · '+number(a.response_time_ms,0)+' ms':'Status belum terkini'),
 metric('SUHU CPU',number(n.cpu_temperature_c)+' °C','RAM tersedia '+number(n.memory_available_mb,0)+' MB'),
 metric('TEKANAN ESTIMASI',number(a.pressure_kpa_estimated,2)+' kPa','Estimasi atmosfer · Open-Meteo'),
 metric('SOLENOID',a.solenoid_on==='0'?'OFF':a.solenoid_on==='1'?'ON':'—','Status kiriman · tanpa kontrol fisik'));
 lastNetwork=Date.now();
 }catch(e){$('#api-summary').textContent='API · status tidak tersedia';$('#wifi-summary').textContent='Jaringan · gagal memperbarui';$('#network-time').textContent='Gagal memperbarui · mencoba kembali';document.querySelectorAll('.auxiliary').forEach(el=>{el.classList.add('stale');el.querySelector('.sensor-foot small').textContent='Status tidak terkini'})}}
async function pollState(){try{lastState=await get('/api/state');lastReceived=Date.now();renderState(lastState)}catch(e){if(lastState)renderState(lastState);$('#connection').textContent='● ThermoVue tidak terjangkau';$('#connection').className='pill bad'}finally{setTimeout(pollState,2000)}}
async function networkLoop(){await pollNetwork();setTimeout(networkLoop,5000)}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===b.dataset.view));document.querySelectorAll('[data-view]').forEach(n=>n.classList.toggle('selected',n===b))}));
setInterval(()=>{$('#clock').textContent=clock(Date.now()/1000)+' WIB';if(lastState&&Date.now()-lastReceived>4000)renderState(lastState);if(lastNetwork&&Date.now()-lastNetwork>15000){$('#api-summary').textContent='API · status tidak terkini';$('#network-time').textContent='Status jaringan tidak terkini';document.querySelectorAll('.auxiliary').forEach(el=>el.classList.add('stale'))}},1000);
