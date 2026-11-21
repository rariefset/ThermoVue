'use strict';
globalThis.ThermoCalibration = {
 apply(raw, sensor={}) {
  if(raw==null)return null;
  const cal=sensor.calibration||{}, p=cal.points||[];
  if(cal.method!=='piecewise_linear'||p.length<3)return raw+Number(sensor.offset||0);
  if(raw<=p[0].raw_c)return raw+p[0].reference_c-p[0].raw_c;
  if(raw>=p[p.length-1].raw_c)return raw+p[p.length-1].reference_c-p[p.length-1].raw_c;
  for(let i=1;i<p.length;i++)if(raw<=p[i].raw_c){const t=(raw-p[i-1].raw_c)/(p[i].raw_c-p[i-1].raw_c);return p[i-1].reference_c*(1-t)+p[i].reference_c*t}
 },
 validate(points) {
  if(points.length<3||points.length>20)throw Error('Isi minimal 3 dan maksimal 20 titik.');
  const sorted=points.map(p=>({...p})).sort((a,b)=>a.raw_c-b.raw_c);
  if(sorted.some(p=>!Number.isFinite(p.raw_c)||!Number.isFinite(p.reference_c)))throw Error('Lengkapi semua suhu mentah dan referensi.');
  for(let i=1;i<sorted.length;i++){
   if(sorted[i].raw_c<=sorted[i-1].raw_c)throw Error('Suhu mentah setiap titik harus berbeda.');
   if(sorted[i].reference_c<=sorted[i-1].reference_c)throw Error('Suhu referensi harus meningkat mengikuti suhu mentah.');
  }
  return sorted;
 }
};
