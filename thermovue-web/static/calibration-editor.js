'use strict';
let calBusy=false;
const calDrafts={};
let calInitialModelLoaded=false;
const calSensor=()=>Number($('#cal-sensor').value);
const tableMode=()=>$('#cal-method').value==='table';
function draftRows(){
 const id=calSensor();
 if(!calDrafts[id]){
  let stored=null;try{stored=JSON.parse(localStorage.getItem('thermovue-calibration-draft-'+id))}catch(e){}
  if(Array.isArray(stored)&&stored.length>=3&&stored.length<=20&&stored.every(p=>p&&typeof p.raw==='string'&&typeof p.reference==='string'))calDrafts[id]=stored;
  else {const points=lastState?.config?.['sensor'+id]?.calibration?.points;calDrafts[id]=points?.length>=3?points.map(p=>({raw:String(p.raw_c),reference:String(p.reference_c)})):Array.from({length:3},()=>({raw:'',reference:''}))}
 }
 return calDrafts[id];
}
function storeDraft(){try{localStorage.setItem('thermovue-calibration-draft-'+calSensor(),JSON.stringify(draftRows()))}catch(e){}}
function tablePoints(){return ThermoCalibration.validate(draftRows().map(p=>({raw_c:p.raw.trim()===''?NaN:Number(p.raw),reference_c:p.reference.trim()===''?NaN:Number(p.reference)})))}
function rawState(){
 const id=calSensor(),s=lastState,raw=s?.['s'+id],timestamp=s?.timestamps?.[id-1]||0;
 return {raw,fresh:typeof raw==='number'&&Number.isFinite(raw)&&s?.['s'+id+'_connected']&&timestamp>0&&s.server_time-timestamp+(Date.now()-lastReceived)/1000<=4};
}
function renderRows(){
 const body=$('#cal-rows');body.replaceChildren();
 draftRows().forEach((point,index)=>{
  const row=document.createElement('tr'),n=document.createElement('td');n.textContent=String(index+1);row.append(n);
  for(const key of ['raw','reference']){
   const td=document.createElement('td'),input=document.createElement('input');input.type='number';input.step='any';input.inputMode='decimal';input.value=point[key];input.setAttribute('aria-label',(key==='raw'?'Suhu mentah':'Suhu referensi')+' titik '+(index+1));
   input.addEventListener('input',()=>{point[key]=input.value;storeDraft();renderCalibration()});td.append(input);row.append(td);
  }
  const actions=document.createElement('td'),capture=document.createElement('button'),remove=document.createElement('button');capture.type=remove.type='button';capture.textContent='Ambil';capture.className='capture-raw';capture.setAttribute('aria-label','Ambil suhu mentah titik '+(index+1));
  capture.addEventListener('click',()=>{const current=rawState();if(!current.fresh)return;point.raw=String(current.raw);storeDraft();renderRows();renderCalibration()});
  remove.textContent='×';remove.setAttribute('aria-label','Hapus titik '+(index+1));remove.disabled=draftRows().length<=3||calBusy;
  remove.addEventListener('click',()=>{draftRows().splice(index,1);storeDraft();renderRows();renderCalibration()});actions.append(capture,remove);row.append(actions);body.append(row);
 });
}
function renderCalibration(){
 const cfg=lastState?.config?.['sensor'+calSensor()]||{},cal=cfg.calibration||{},current=rawState(),multi=tableMode();
 if(lastState&&!calInitialModelLoaded){
  calInitialModelLoaded=true;
  for(const id of [1,2]){
   let stored=false;try{stored=localStorage.getItem('thermovue-calibration-draft-'+id)!==null}catch(e){}
   const points=lastState.config?.['sensor'+id]?.calibration?.points;
   if(!stored&&points?.length>=3&&(!calDrafts[id]||calDrafts[id].every(p=>p.raw===''&&p.reference==='')))calDrafts[id]=points.map(p=>({raw:String(p.raw_c),reference:String(p.reference_c)}));
  }
  renderRows();
 }
 if(!$('#cal-rows').children.length&&lastState)renderRows();
 $('#single-reference').hidden=multi;$('#cal-table-panel').hidden=!multi;$('#cal-load').hidden=!multi;
 $('#cal-reference').required=!multi;
 $('#cal-raw').textContent=number(current.raw)+' °C'+(current.fresh?'':' · tidak terkini');
 $('#cal-offset').textContent=cal.method==='piecewise_linear'?(cal.points?.length||0)+' titik':'Offset '+number(cfg.offset||0,2)+' °C';
 let valid=false,preview='—';
 if(multi){try{const points=tablePoints();valid=true;$('#cal-table-status').textContent=points.length+' titik valid · draft lokal';if(current.fresh)preview=number(ThermoCalibration.apply(current.raw,{calibration:{method:'piecewise_linear',points}}),2)+' °C'}catch(e){$('#cal-table-status').textContent=e.message}}
 else {const reference=$('#cal-reference').valueAsNumber;valid=current.fresh&&Number.isFinite(reference);if(valid)preview=number(reference,2)+' °C (Δ '+number(reference-current.raw,2)+')'}
 $('#cal-preview').textContent=preview;$('#cal-save').disabled=!valid||calBusy;$('#cal-reset').disabled=calBusy;$('#cal-load').disabled=calBusy;$('#cal-add').disabled=draftRows().length>=20||calBusy;
 document.querySelectorAll('.capture-raw').forEach(b=>b.disabled=!current.fresh||calBusy);
 $('#cal-last').textContent=cal.calibrated_at?'Tersimpan '+new Date(cal.calibrated_at*1000).toLocaleString('id-ID',{timeZone:'Asia/Jakarta'}):'Belum ada catatan kalibrasi';
 $('#cal-help').textContent=multi?'Isi pasangan mentah–referensi saat stabil. Antar titik: interpolasi linear. Di luar rentang: offset titik terdekat. Tidak mengubah kompensasi transmitter.':'Tempatkan probe dan alat referensi pada suhu sama. Koreksi satu titik = suhu mentah + offset. Tabel aktif akan diganti bila disimpan.';
}
async function saveCalibration(reset=false){
 if(calBusy)return;
 const sensor=calSensor();let payload;
 if(reset){if(!confirm('Hapus koreksi aktif Suhu '+sensor+' dan kembalikan ke suhu mentah?'))return;payload={sensor,reset:true}}
 else if(tableMode()){try{payload={sensor,points:tablePoints()}}catch(e){$('#cal-feedback').textContent=e.message;return}}
 else {if(!rawState().fresh||!Number.isFinite($('#cal-reference').valueAsNumber))return;payload={sensor,reference_c:$('#cal-reference').valueAsNumber}}
 calBusy=true;renderCalibration();$('#cal-feedback').textContent='Menyimpan…';
 try{
  const r=await fetch('/api/calibration',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(8000)});
  const data=await r.json();if(!r.ok)throw Error(data.error||'Gagal menyimpan');
  if(lastState){lastState.config=data.config;renderState(lastState)}
  $('#cal-feedback').textContent=reset?'Koreksi direset.':payload.points?'Tabel '+payload.points.length+' titik tersimpan.':'Offset tersimpan.';
 }catch(e){$('#cal-feedback').textContent=e.name==='TimeoutError'?'Respons belum diterima. Periksa model aktif sebelum mencoba lagi.':e.message}
 finally{calBusy=false;renderCalibration()}
}
$('#calibration-form').addEventListener('submit',e=>{e.preventDefault();saveCalibration()});
$('#cal-reset').addEventListener('click',()=>saveCalibration(true));
$('#cal-reference').addEventListener('input',renderCalibration);
$('#cal-sensor').addEventListener('change',()=>{$('#cal-feedback').textContent='';renderRows();renderCalibration()});
$('#cal-method').addEventListener('change',renderCalibration);
$('#cal-add').addEventListener('click',()=>{if(draftRows().length>=20)return;draftRows().push({raw:'',reference:''});storeDraft();renderRows();renderCalibration();$('#cal-rows tr:last-child input').focus()});
$('#cal-load').addEventListener('click',()=>{const points=lastState?.config?.['sensor'+calSensor()]?.calibration?.points;if(!points?.length){$('#cal-feedback').textContent='Belum ada tabel tersimpan untuk sensor ini.';return}calDrafts[calSensor()]=points.map(p=>({raw:String(p.raw_c),reference:String(p.reference_c)}));storeDraft();renderRows();renderCalibration();$('#cal-feedback').textContent='Tabel tersimpan dimuat ke draft.'});
renderRows();renderCalibration();pollState();networkLoop();
