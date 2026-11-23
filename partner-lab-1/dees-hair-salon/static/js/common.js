/* Shared client helpers. Appointment rules are enforced by the Python server. */
(() => {
  'use strict';
  const initial = JSON.parse(document.getElementById('salon-config').textContent);
  const S = window.Salon = {config:initial};
  S.api = async function(path, data, method) {
    if (window.DeesPreview) return window.DeesPreview.request(path, data, method);
    const options = {method:method || (data ? 'POST':'GET'),headers:{'Accept':'application/json'},cache:'no-store'};
    if (data) {options.headers['Content-Type']='application/json'; options.headers['X-CSRF-Token']=S.config.csrf_token; options.body=JSON.stringify(data);}
    let response;
    try { response=await fetch(path,options); } catch (_) { throw new Error('We could not reach the salon. Please check your connection and try again.'); }
    const result=await response.json().catch(()=>({error:'Something went wrong. Please refresh and try again.'}));
    if(!response.ok) throw new Error(result.error || 'We could not complete that request.');
    return result;
  };
  S.ready = S.api('/api/config').then(config=>{ S.config=config; return config; });
  S.ready.catch(()=>{});
  S.date = iso=>new Date(`${iso}T12:00:00`);
  S.formatDate = (iso,short=false)=>S.date(iso).toLocaleDateString('en-US',short?{month:'short',day:'numeric'}:{weekday:'long',month:'long',day:'numeric',year:'numeric'});
  S.formatTime = time=>{const [h,m]=time.split(':').map(Number);return `${h%12||12}:${String(m).padStart(2,'0')} ${h<12?'AM':'PM'}`;};
  S.money = value=>`$${Number(value).toLocaleString('en-US',{maximumFractionDigits:2})}`;
  S.iso = date=>`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  S.service=id=>S.config.services.find(s=>s.id===id);
  S.stylist=id=>S.config.stylists.find(s=>s.id===id);
  S.showError = (id,error)=>{const el=document.getElementById(id);if(el){el.textContent=error?error.message||error:'';el.hidden=!error;}};
  S.busy = (button,busy,label)=>{button.disabled=busy;if(busy){button.dataset.original=button.textContent;button.textContent=label||'Please wait…';}else button.textContent=button.dataset.original||button.textContent;};
  S.slotButtons=(container,slots,chosen,onSelect)=>{
    container.replaceChildren();
    if(!slots.length){const p=document.createElement('p');p.textContent='No available times on this day. Please try another day or stylist.';container.append(p);return;}
    slots.forEach(slot=>{const btn=document.createElement('button');btn.type='button';btn.className=`time-slot${chosen===slot.time?' selected':''}`;btn.textContent=slot.label||S.formatTime(slot.time);btn.setAttribute('aria-pressed',String(chosen===slot.time));btn.addEventListener('click',()=>{onSelect(slot.time);container.querySelectorAll('.time-slot').forEach(b=>{b.classList.toggle('selected',b===btn);b.setAttribute('aria-pressed',String(b===btn));});});container.append(btn);});
  };
})();
