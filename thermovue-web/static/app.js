'use strict';
const $ = s => document.querySelector(s);
let state = null, config = null, geometry = null;
let chartRange = {mode:'relative', seconds:300}, trend = null, trendRequest = 0, trendFetching = false;
let trendLoadedAt = 0, trendError = '';
let fetching = false, toastTimer;
const colors = ['#247b64', '#7a6aaf'];
const timeText = t => new Date(t * 1000).toLocaleTimeString('id-ID', {timeZone:'Asia/Jakarta',hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'}).replaceAll('.', ':');
const number = v => Number(v).toFixed(1);
const dateTimeText = t => new Date(t*1000).toLocaleString('id-ID', {timeZone:'Asia/Jakarta',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).replaceAll('.', ':');
const wibInput = t => new Date(t*1000+7*3600000).toISOString().slice(0,19);
function chartBounds() {
 const now=state?.server_time||Date.now()/1000;
 if(chartRange.mode==='custom')return {start:chartRange.start,end:chartRange.end};
 if(chartRange.mode==='today')return {start:Date.parse(wibInput(now).slice(0,10)+'T00:00:00+07:00')/1000,end:now};
 return {start:now-chartRange.seconds,end:now};
}
function chartPoints(start,end) {
 return [0,1].map(i=>{
  const points=new Map((trend?.series?.[i]||[]).filter(p=>p[0]>=start&&p[0]<=end).map(p=>[p[0],p]));
  (state?.history?.[i]||[]).forEach(([t,v])=>{if(t>=start&&t<=end)points.set(t,[t,ThermoCalibration.apply(v,config?.[`sensor${i+1}`]),'live']);});
  return [...points.values()].sort((a,b)=>a[0]-b[0]);
 });
}
async function loadTrend(force=false) {
 if(trendFetching&&!force)return;
 const id=++trendRequest,bounds=chartBounds();trendFetching=true;
 $('#chart-source').textContent='Memuat riwayat grafik…';
 try {
  const result=await jsonFetch('/api/trend?'+new URLSearchParams({...bounds,max_points:1800}));
  if(id!==trendRequest)return;
  trend=result;trendLoadedAt=Date.now();trendError='';drawChart();
 }catch(error){if(id===trendRequest){trendError='Riwayat grafik gagal dimuat. Klik ↻ untuk mencoba lagi.';drawChart();}}
 finally{if(id===trendRequest)trendFetching=false;}
}
function applyChartRange(selection,label) {
 chartRange=selection;trend=null;trendError='';trendLoadedAt=0;
 $('#chart-range-label').textContent=label;
 $('#chart-range-picker').open=false;
 document.querySelectorAll('[data-window]').forEach(b=>b.classList.toggle('selected',selection.mode==='today'?b.dataset.window==='today':selection.mode==='relative'&&Number(b.dataset.window)===selection.seconds));
 try{localStorage.setItem('thermovue-chart-range',JSON.stringify({...selection,label}));}catch{}
 drawChart();loadTrend(true);
}
async function jsonFetch(path, options = {}) {
 const response = await fetch(path, {...options, signal:AbortSignal.timeout(3500)});
 const body = await response.json();
 if (!response.ok) throw new Error(body.error || 'Permintaan gagal. Coba lagi.');
 return body;
}
function toast(message) {
 clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false;
 toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}
function render() {
 if (!state || !config) return;
 syncArchiveSettings();
 const count = Number(state.s1 != null) + Number(state.s2 != null);
 $('#connection').textContent = count === 2 ? '● Semua sensor terhubung' : `● ${count}/2 sensor terhubung`;
 $('#connection').classList.toggle('warning', count !== 2);
 for (let i=0;i<2;i++) {
  const card = $(`#sensor${i+1}`), cfg = config[`sensor${i+1}`];
  const raw = state[`s${i+1}`], value = raw == null ? null : ThermoCalibration.apply(raw,cfg);
  const badge = card.querySelector('[data-status]');
  let label = 'Normal', cls = '';
  if (value == null) { label = 'Tidak ada data'; cls = 'missing'; }
  else if (value >= cfg.high_alarm) {label = 'Alarm tinggi'; cls = 'high';}
  else if (value <= cfg.low_alarm) {label = 'Alarm rendah'; cls = 'low';}
  badge.textContent = label; badge.className = 'badge ' + cls;
  card.classList.toggle('disconnected', value == null);
  card.querySelector('[data-value]').textContent = value == null ? '—' : number(value);
  card.querySelector('[data-range]').textContent = `Batas ${cfg.low_alarm} – ${cfg.high_alarm} °C`;
  const timestamp = state.timestamps[i];
  card.querySelector('[data-updated]').textContent = value == null ? 'Mencoba kembali…' : timeText(timestamp);
  const marker = card.querySelector('[data-marker]');
  marker.style.left = `${value == null ? 0 : Math.max(2, Math.min(98, (value-cfg.low_alarm)/(cfg.high_alarm-cfg.low_alarm)*100))}%`;
  marker.hidden = value == null;
  card.querySelector('[data-id]').textContent = 'ID ' + (i === 0 ? config.modbus.slave_id : config.modbus.sensor2_slave_id);
 }
 $('#delta').textContent = count === 2 ? number(Math.abs(ThermoCalibration.apply(state.s1,config.sensor1)-ThermoCalibration.apply(state.s2,config.sensor2))) : '—';
 $('#last-update').textContent = count ? timeText(state.timestamp) : 'Menunggu';
 $('#baudrate').textContent = config.modbus.baudrate + ' baud';
 drawChart();
}
async function refresh() {
 if(fetching) return;
 fetching = true;
 try {
  state = await jsonFetch('/api/state'); config = state.config;
  $('#offline').hidden = true; render();
  if(!trendFetching && (!trendLoadedAt || chartRange.mode!=='custom'&&Date.now()-trendLoadedAt>15000))loadTrend();
 } catch(error) {
  $('#offline').hidden = false;
  if (state) {state = {...state,s1:null,s2:null,connected:false}; render();}
  else {$('#connection').textContent = '● Pi tidak terhubung';$('#connection').classList.add('warning');}
 } finally { fetching = false; }
}
function drawChart() {
 const canvas = $('#chart'), width = canvas.clientWidth, height = canvas.clientHeight;
 if(!width || !height) return;
 const dpr = window.devicePixelRatio || 1;
 canvas.width = Math.round(width*dpr); canvas.height = Math.round(height*dpr);
 const ctx = canvas.getContext('2d'); ctx.scale(dpr,dpr);
 const {start,end:now}=chartBounds();
 const data = chartPoints(start,now);
 const all = data.flat(), values = all.map(p=>p[1]);
 $('#sample-count').textContent = all.length.toLocaleString('id-ID');
 $('#chart-empty').hidden = all.length > 0;
 $('#chart-empty').textContent=trendFetching&&!trend?'Memuat data…':'Tidak ada data pada rentang ini.';
 $('#chart-period').textContent=`${dateTimeText(start)} → ${dateTimeText(now)} WIB`;
 $('#chart-source').textContent=trendError||`${chartRange.mode==='custom'?'Rentang tetap':'● Live'} · Riwayat tersimpan setiap ${trend?.archive_interval_minutes??config?.logging?.interval_minutes??1} menit + data langsung terbaru. Celah tanpa data ditampilkan terputus.`;
 let min = values.length ? Math.floor((Math.min(...values)-.5)*2)/2 : 0;
 let max = values.length ? Math.ceil((Math.max(...values)+.5)*2)/2 : 40;
 const area = {left:37,right:width-12,top:18,bottom:height-30};
 const x = t => area.left+(t-start)/(now-start)*(area.right-area.left);
 const y = v => area.bottom-(v-min)/(max-min)*(area.bottom-area.top);
 ctx.font = '9px Arial'; ctx.lineWidth = 1;
 for(let i=0;i<=4;i++) {
  const value = min+(max-min)*i/4, yy = y(value);
  ctx.strokeStyle = '#ecf0e8';ctx.setLineDash([3,4]);ctx.beginPath();ctx.moveTo(area.left,yy);ctx.lineTo(area.right,yy);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle = '#94a08f';ctx.textAlign = 'right';ctx.fillText(value.toFixed(1),area.left-10,yy+3);
 }
 for(let i=0;i<=3;i++) {
  const timestamp = start+(now-start)*i/3;
  ctx.fillStyle = '#94a08f';ctx.textAlign = i===0?'left':i===3?'right':'center';
  const tick=now-start>=86400?new Date(timestamp*1000).toLocaleString('id-ID',{timeZone:'Asia/Jakarta',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).replaceAll('.',':'):timeText(timestamp).slice(0,5);
  ctx.fillText(tick,x(timestamp),height-9);
 }
 data.forEach((points,i) => {
  if (!points.length) return;
  ctx.strokeStyle=colors[i];ctx.lineWidth=2;ctx.globalAlpha=1;
  ctx.beginPath();
  points.forEach((p,j)=>{
   const previous=points[j-1],gap=previous&&(p[2]==='archive'||previous[2]==='archive')?Math.max(5,(trend?.archive_interval_minutes||10)*90):5;
   if(!j||p[0]-previous[0]>gap)ctx.moveTo(x(p[0]),y(p[1]));else ctx.lineTo(x(p[0]),y(p[1]));
  });ctx.stroke();
  // Sparse archived points remain visible even when they cannot be joined.
  if(points.length<300)points.forEach(p=>{ctx.fillStyle=colors[i];ctx.beginPath();ctx.arc(x(p[0]),y(p[1]),2,0,2*Math.PI);ctx.fill();});
  const end=points.at(-1);ctx.fillStyle=colors[i];ctx.beginPath();ctx.arc(x(end[0]),y(end[1]),3,0,2*Math.PI);ctx.fill();ctx.globalAlpha=1;
 });
 geometry={data,area,start,end:now,x};
}
$('#chart').addEventListener('pointermove',event=>{
 if(!geometry || !geometry.data.flat().length) return;
 const rect=event.currentTarget.getBoundingClientRect(), xx=event.clientX-rect.left;
 const timestamp=geometry.start+(xx-geometry.area.left)/(geometry.area.right-geometry.area.left)*(geometry.end-geometry.start);
 const rows=[];
 geometry.data.forEach((points,i)=>{
  if(!points.length)return;
  const point=points.reduce((best,p)=>Math.abs(p[0]-timestamp)<Math.abs(best[0]-timestamp)?p:best);
  rows.push(`S${i+1}: ${number(point[1])} °C · ${dateTimeText(point[0])} WIB`);
 });
 const tip=$('#tooltip');tip.replaceChildren(...rows.map(row=>{const div=document.createElement('div');div.textContent=row;return div;}));
 tip.style.display='block';tip.style.left=Math.max(0,Math.min(xx+8,rect.width-180))+'px';tip.style.top='12px';
});
$('#chart').addEventListener('pointerleave',()=>$('#tooltip').style.display='none');
new ResizeObserver(drawChart).observe($('#chart'));
document.querySelectorAll('[data-window]').forEach(button=>button.addEventListener('click',()=>{
 const value=button.dataset.window;
 applyChartRange(value==='today'?{mode:'today'}:{mode:'relative',seconds:Number(value)},button.textContent);
}));
$('#chart-refresh').addEventListener('click',()=>loadTrend(true));
$('#chart-range-picker').addEventListener('toggle',()=>{
 if($('#chart-range-picker').open){const bounds=chartBounds();$('#chart-start').value=wibInput(bounds.start);$('#chart-end').value=wibInput(bounds.end);$('#chart-range-error').hidden=true;}
});
document.addEventListener('click',event=>{const picker=$('#chart-range-picker');if(picker.open&&!picker.contains(event.target))picker.open=false;});
document.addEventListener('keydown',event=>{if(event.key==='Escape')$('#chart-range-picker').open=false;});
$('#chart-custom-range').addEventListener('submit',event=>{
 event.preventDefault();
 const start=Date.parse($('#chart-start').value+'+07:00')/1000,end=Date.parse($('#chart-end').value+'+07:00')/1000;
 const error=$('#chart-range-error');
 if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end||end-start>31*86400){error.textContent='Waktu awal harus sebelum waktu akhir. Maksimal 31 hari.';error.hidden=false;return;}
 applyChartRange({mode:'custom',start,end},'Rentang khusus');
});
try{
 const saved=JSON.parse(localStorage.getItem('thermovue-chart-range'));
 if(saved&&(saved.mode==='today'||saved.mode==='relative'&&[300,900,1800,3600,10800,21600,43200,86400,172800,604800].includes(saved.seconds)||saved.mode==='custom'&&Number.isFinite(saved.start)&&Number.isFinite(saved.end)&&saved.end>saved.start&&saved.end-saved.start<=31*86400)){
  chartRange=saved;$('#chart-range-label').textContent=saved.mode==='custom'?'Rentang khusus':saved.mode==='today'?'Hari ini':document.querySelector(`[data-window="${saved.seconds}"]`).textContent;
 }
}catch{}
document.querySelectorAll('[data-window]').forEach(b=>b.classList.toggle('selected',chartRange.mode==='today'?b.dataset.window==='today':chartRange.mode==='relative'&&Number(b.dataset.window)===chartRange.seconds));
const dialog=$('#settings'),form=$('#settings-form');
document.querySelectorAll('[data-settings]').forEach(button=>button.addEventListener('click',()=>{
 if(!config){toast('Tunggu koneksi ke Raspberry Pi sebelum mengatur batas.');return;}
 for(let i=1;i<=2;i++) {
  form.elements[`low${i}`].value=config[`sensor${i}`].low_alarm;
  form.elements[`high${i}`].value=config[`sensor${i}`].high_alarm;
 }
 $('#form-error').textContent='';dialog.showModal();
}));
$('#close-settings').addEventListener('click',()=>dialog.close());
$('#cancel-settings').addEventListener('click',()=>dialog.close());
form.addEventListener('submit',async event=>{
 event.preventDefault();$('#form-error').textContent='';
 const payload={};
 for(let i=1;i<=2;i++) {
  const low=Number(form.elements[`low${i}`].value), high=Number(form.elements[`high${i}`].value);
  if(!Number.isFinite(low)||!Number.isFinite(high)||low>=high){$('#form-error').textContent=`Sensor ${i}: batas bawah harus lebih kecil dari batas atas.`;return;}
  payload[`sensor${i}`]={low_alarm:low,high_alarm:high};
 }
 const save=$('#save-settings');save.disabled=true;$('#cancel-settings').disabled=true;
 try {
  const result=await jsonFetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  config=result.config;render();dialog.close();toast('Batas suhu tersimpan. Aplikasi Pi ikut diperbarui.');
 }catch(error){$('#form-error').textContent=error.message;}
 finally{save.disabled=false;$('#cancel-settings').disabled=false;}
});
function tick(){const now=new Date();$('#clock').textContent=timeText(now.getTime()/1000)+' WIB';$('#date').textContent=now.toLocaleDateString('id-ID',{timeZone:'Asia/Jakarta',day:'numeric',month:'long',year:'numeric'});}
tick();setInterval(tick,1000);refresh();setInterval(refresh,1200);

// Stable, server-side pagination: incoming samples never shift a page while reading.
let historyPage = 1, historyResult = null, historySnapshot = null, historyRequest = 0;
let historyFilters = {}, historyBusy = false;
const historyForm = $('#history-filter');
const todayWib = new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Jakarta',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
$('#history-start').value = todayWib+'T00:00:00';
$('#history-end').value = todayWib+'T23:59:59';
function chosenFilters(){return {start:$('#history-start').value,end:$('#history-end').value};}
function historyParams(){const params=new URLSearchParams(historyFilters);if(historySnapshot!==null)params.set('snapshot',historySnapshot);return params;}
function tableMessage(message){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=8;td.className='table-empty';td.textContent=message;tr.append(td);$('#history-rows').replaceChildren(tr);}
function enableHistoryControls(){
 $('#history-prev').disabled=historyBusy||!historyResult||historyResult.page<=1;
 $('#history-next').disabled=historyBusy||!historyResult||historyResult.page>=historyResult.pages;
 document.querySelectorAll('[data-export]').forEach(button=>button.disabled=historyBusy||!historyResult);
 $('#history-refresh').disabled=historyBusy;
}
async function loadHistory(page=1,reset=false){
 const requestId=++historyRequest;historyBusy=true;enableHistoryControls();
 if(reset)historySnapshot=null;
 const params=historyParams();params.set('page',page);params.set('page_size',$('#history-size').value);
 $('#history-summary').textContent='Memuat riwayat…';$('#history-error').hidden=true;
 try{
  const result=await jsonFetch('/api/readings?'+params);
  if(requestId!==historyRequest)return;
  historyResult=result;historyPage=result.page;historySnapshot=result.snapshot;
  const fragment=document.createDocumentFragment();
  result.rows.forEach((row,index)=>{
   const tr=document.createElement('tr');
   const values=[(result.page-1)*result.page_size+index+1,row.time,`Sensor ${row.sensor}`,row.device_id,number(row.temperature),row.low_alarm??'—',row.high_alarm??'—',row.status];
   values.forEach((value,col)=>{
    const td=document.createElement('td');
    if(col===2||col===7){const span=document.createElement('span');span.textContent=value;
     span.className=col===2?'table-sensor'+(row.sensor===2?' two':''):'table-status'+(value==='Alarm tinggi'?' high':value==='Alarm rendah'?' low':value==='Normal'?'':' unknown');td.append(span);
    }else{td.textContent=value;if(col===4)td.className='temperature-cell';}
    tr.append(td);
   });fragment.append(tr);
  });
  $('#history-rows').replaceChildren(fragment);
  if(!result.total)tableMessage('Tidak ada data pada periode ini. Coba ubah tanggal atau jam.');
  const begin=result.total?(result.page-1)*result.page_size+1:0,end=Math.min(result.page*result.page_size,result.total);
  $('#history-summary').textContent=`${begin.toLocaleString('id-ID')}–${end.toLocaleString('id-ID')} dari ${result.total.toLocaleString('id-ID')} data · Halaman ${result.page}/${result.pages}`;
  const pages=new Set([1,result.pages]);for(let n=result.page-1;n<=result.page+1;n++)if(n>=1&&n<=result.pages)pages.add(n);
  const buttons=document.createDocumentFragment();let previous=0;
  [...pages].sort((a,b)=>a-b).forEach(n=>{
   if(previous&&n-previous>1){const dots=document.createElement('span');dots.textContent='…';buttons.append(dots);}
   const button=document.createElement('button');button.type='button';button.textContent=n;button.setAttribute('aria-label','Halaman '+n);
   if(n===result.page)button.setAttribute('aria-current','page');button.addEventListener('click',()=>loadHistory(n));buttons.append(button);previous=n;
  });$('#history-pages').replaceChildren(buttons);
 }catch(error){
  if(requestId!==historyRequest)return;
  historyResult=null;tableMessage('Riwayat belum dapat ditampilkan.');$('#history-pages').replaceChildren();
  $('#history-summary').textContent='Gagal mengambil riwayat';$('#history-error').textContent=error.message;$('#history-error').hidden=false;
 }finally{if(requestId===historyRequest){historyBusy=false;enableHistoryControls();}}
}
historyForm.addEventListener('submit',event=>{event.preventDefault();historyFilters=chosenFilters();loadHistory(1,true);});
$('#history-clear').addEventListener('click',()=>{$('#history-start').value='';$('#history-end').value='';historyFilters={};loadHistory(1,true);});
$('#history-refresh').addEventListener('click',()=>loadHistory(1,true));
$('#history-size').addEventListener('change',()=>loadHistory(1));
$('#history-prev').addEventListener('click',()=>loadHistory(historyPage-1));
$('#history-next').addEventListener('click',()=>loadHistory(historyPage+1));
document.querySelectorAll('[data-export]').forEach(button=>button.addEventListener('click',async()=>{
 const format=button.dataset.export,params=historyParams();button.disabled=true;const label=button.textContent;button.textContent='Menyiapkan…';
 try{
  const response=await fetch('/api/export.'+format+'?'+params,{signal:AbortSignal.timeout(120000)});
  if(!response.ok){const error=await response.json();throw new Error(error.error||'Unduhan gagal.');}
  const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=response.headers.get('Content-Disposition')?.match(/filename=([^;]+)/)?.[1]||'thermovue.'+format;
  document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  toast(`File ${format.toUpperCase()} siap, berisi seluruh data sesuai filter aktif.`);
 }catch(error){$('#history-error').textContent=error.message;$('#history-error').hidden=false;}
 finally{button.textContent=label;enableHistoryControls();}
}));
historyFilters=chosenFilters();loadHistory();


function syncArchiveSettings(){
 const input=$('#archive-interval');
 if(!input||!config)return;
 const minutes=config.logging?.interval_minutes??1;
 $('#archive-status').textContent=`Aktif: setiap ${minutes} menit per sensor. Pengaturan tersimpan setelah restart.`;
 if(!input.dataset.dirty && document.activeElement!==input)input.value=minutes;
}
$('#archive-interval').addEventListener('input',event=>{event.target.dataset.dirty='true';$('#archive-feedback').hidden=true;});
$('#archive-settings').addEventListener('submit',async event=>{
 event.preventDefault();
 const input=$('#archive-interval'),minutes=Number(input.value),feedback=$('#archive-feedback'),button=$('#archive-save');
 feedback.hidden=false;feedback.classList.remove('error');
 if(!Number.isInteger(minutes)||minutes<1||minutes>1440){feedback.textContent='Isi bilangan bulat antara 1 dan 1440 menit.';feedback.classList.add('error');return;}
 button.disabled=true;feedback.textContent='Menyimpan interval…';
 try{
  const result=await jsonFetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({logging:{interval_minutes:minutes}})});
  config=result.config;delete input.dataset.dirty;input.value=config.logging.interval_minutes;syncArchiveSettings();
  feedback.textContent=`Tersimpan. Pencatatan berikutnya dihitung ${minutes} menit dari catatan terakhir masing-masing sensor.`;
  toast('Interval penyimpanan riwayat berhasil disimpan.');
 }catch(error){feedback.textContent=error.message;feedback.classList.add('error');}
 finally{button.disabled=false;}
});

// Diagnostics come from the existing bridge logs; no extra serial or ping worker.
let networkFetching=false;
const metric=(value,digits=1)=>value!==''&&value!=null&&Number.isFinite(Number(value))?Number(value).toFixed(digits):'—';
const logTime=value=>{const stamp=Date.parse(value);return Number.isFinite(stamp)?dateTimeText(stamp/1000):'—';};
async function loadNetwork(){
 if(networkFetching)return;
 networkFetching=true;
 try{
  const result=await jsonFetch('/api/network'),net=result.network,api=result.api;
  const health=$('#network-health');
  const age=net?(result.server_time-Date.parse(net.network_checked_at_wib)/1000):Infinity;
  const apiAge=api?(result.server_time-Date.parse(api.sent_at_wib)/1000):Infinity;
  health.textContent=!net?'Belum ada log jaringan':age>20?'● Data jaringan belum diperbarui':net.ping_gateway_status==='ok'?'● Gateway terjangkau':'● Periksa koneksi gateway';
  health.classList.toggle('warning',age>20||net?.ping_gateway_status!=='ok');
  $('#net-ssid').textContent=net?.ssid||'Tidak terhubung';
  $('#net-ip').textContent=`${net?.interface||'—'} · IP ${net?.ip_address||'—'}`;
  $('#net-rssi').textContent=net?.rssi_dbm?metric(net.rssi_dbm,0)+' dBm':'—';
  $('#net-quality').textContent='Kualitas '+metric(net?.wifi_quality_pct,0)+'%';
  $('#net-ping').textContent=net?.ping_gateway_status==='ok'?metric(net.ping_gateway_ms,2)+' ms':'Tidak merespons';
  $('#net-gateway').textContent='Gateway '+(net?.gateway||'—');
  $('#net-api-ms').textContent=api?metric(api.response_time_ms,2)+' ms':'—';
  $('#net-http').textContent=api?`HTTP ${api.http_status||'—'} · ${apiAge>30?'Data lama · ':''}${logTime(api.sent_at_wib)} WIB`:'Belum ada log API';
  $('#net-cpu').textContent='Suhu CPU '+metric(net?.cpu_temperature_c)+' °C';
  const uptime=Number(net?.uptime_s);
  $('#net-uptime').textContent=Number.isFinite(uptime)&&net?`Uptime ${Math.floor(uptime/86400)} hari ${Math.floor(uptime%86400/3600)} jam ${Math.floor(uptime%3600/60)} menit`:'Uptime —';
  $('#net-memory').textContent='RAM tersedia '+metric(net?.memory_available_mb,0)+' MB';
  $('#net-checked').textContent='Pemeriksaan '+logTime(net?.network_checked_at_wib)+' WIB';
  const fragment=document.createDocumentFragment();
  result.rows.forEach(row=>{
   const tr=document.createElement('tr');
   [logTime(row.network_checked_at_wib),row.ssid||'—',metric(row.rssi_dbm,0),
    metric(row.ping_gateway_ms,2),row.ping_gateway_status==='ok'?'Berhasil':row.ping_gateway_status==='no_gateway'?'Tanpa gateway':'Gagal',
    metric(row.ping_gateway_loss_pct,0),metric(row.response_time_ms,2),row.http_status||'—'].forEach(value=>{
     const td=document.createElement('td');td.textContent=value;tr.append(td);
    });fragment.append(tr);
  });
  if(!result.rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=8;td.className='table-empty';td.textContent='Log jaringan belum tersedia.';tr.append(td);fragment.append(tr);}
  $('#network-rows').replaceChildren(fragment);
  $('#network-summary').textContent=`${result.rows.length} pemeriksaan jaringan terbaru · Waktu WIB${net?.network_error?' · '+net.network_error:''}`;
 }catch(error){
  $('#network-health').textContent='● Status belum dapat dimuat';$('#network-health').classList.add('warning');
  $('#network-summary').textContent=error.message;
 }finally{networkFetching=false;}
}
$('#network-refresh').addEventListener('click',loadNetwork);
loadNetwork();setInterval(loadNetwork,5000);
function syncNavigation(){document.querySelectorAll('a.nav-link').forEach(link=>{const active=link.getAttribute('href')===(location.hash||'#overview');link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');});}
window.addEventListener('hashchange',syncNavigation);syncNavigation();
document.querySelectorAll('[data-log-download]').forEach(button=>button.addEventListener('click',async()=>{
 const feedback=$('#network-feedback'),label=button.textContent;
 button.disabled=true;button.textContent='Menyiapkan ZIP…';feedback.hidden=false;feedback.textContent='Menyiapkan seluruh log tersimpan. Tunggu sampai unduhan selesai.';
 try{
  const response=await fetch('/api/logs/download?kind='+button.dataset.logDownload,{signal:AbortSignal.timeout(120000)});
  if(!response.ok){const body=await response.json();throw new Error(body.error||'Unduhan log gagal.');}
  const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)/)?.[1]||'thermovue_logs.zip';
  document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  feedback.textContent='ZIP siap diunduh. Isinya mencakup seluruh tanggal log yang tersedia.';
 }catch(error){feedback.textContent=error.message;}
 finally{button.disabled=false;button.textContent=label;}
}));
