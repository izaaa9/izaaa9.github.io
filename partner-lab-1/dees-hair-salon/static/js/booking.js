(() => {
 'use strict';
 const S=window.Salon,$=id=>document.getElementById(id);
 let day='',time='',month,step=1,latestRequest=0,confirmedReference='',saving=false;
 const serviceSelect=$('service-select'),stylistSelect=$('stylist-select');
 const summary=()=>{const service=S.service(serviceSelect.value);$('summary-service').textContent=service.name;$('summary-description').textContent=service.description;$('summary-duration').textContent=`${service.duration} minutes`;$('summary-price').textContent=S.money(service.price);$('summary-stylist').textContent=stylistSelect.value==='any'?'Any available stylist':S.stylist(stylistSelect.value).name;$('summary-date').textContent=day?S.formatDate(day,true):'Choose a day';$('summary-time').textContent=time?S.formatTime(time):'Choose a time';$('continue-booking').disabled=!time;};
 function availableDate(iso){const d=S.date(iso),weekday=(d.getDay()+6)%7;return iso>=S.config.min_date&&iso<=S.config.max_date&&!!S.config.hours[String(weekday)];}
 function renderCalendar(){
   const calendar=$('booking-calendar');calendar.replaceChildren();
   const header=document.createElement('div');header.className='calendar-header';
   const prev=document.createElement('button');prev.type='button';prev.className='calendar-arrow';prev.textContent='‹';prev.setAttribute('aria-label','Previous month');
   const next=document.createElement('button');next.type='button';next.className='calendar-arrow';next.textContent='›';next.setAttribute('aria-label','Next month');
   const title=document.createElement('strong');title.textContent=month.toLocaleDateString('en-US',{month:'long',year:'numeric'});
   prev.disabled=S.iso(new Date(month.getFullYear(),month.getMonth(),1)).slice(0,7)<=S.config.min_date.slice(0,7);
   next.disabled=S.iso(new Date(month.getFullYear(),month.getMonth(),1)).slice(0,7)>=S.config.max_date.slice(0,7);
   prev.onclick=()=>{month=new Date(month.getFullYear(),month.getMonth()-1,1);renderCalendar();};next.onclick=()=>{month=new Date(month.getFullYear(),month.getMonth()+1,1);renderCalendar();};
   header.append(prev,title,next);calendar.append(header);
   const grid=document.createElement('div');grid.className='calendar-grid';
   ['S','M','T','W','T','F','S'].forEach((d,i)=>{const el=document.createElement('span');el.className='calendar-weekday';el.textContent=d;el.setAttribute('aria-label',['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][i]);grid.append(el);});
   const first=new Date(month.getFullYear(),month.getMonth(),1),last=new Date(month.getFullYear(),month.getMonth()+1,0);
   for(let i=0;i<first.getDay();i++)grid.append(document.createElement('span'));
   for(let number=1;number<=last.getDate();number++){
     const d=new Date(month.getFullYear(),month.getMonth(),number),iso=S.iso(d),button=document.createElement('button');button.type='button';button.textContent=number;button.className=`calendar-day${iso===day?' selected':''}${iso===S.config.today?' today':''}`;button.disabled=!availableDate(iso);button.setAttribute('aria-label',S.formatDate(iso));button.setAttribute('aria-pressed',String(iso===day));if(iso===S.config.today)button.setAttribute('aria-current','date');button.addEventListener('click',()=>{day=iso;time='';renderCalendar();loadSlots();});grid.append(button);
   }
   calendar.append(grid);
 }
 async function loadSlots(){
   time='';summary();const request=++latestRequest;S.showError('availability-error',null);$('selected-day').textContent=S.formatDate(day);$('time-slots').textContent='Loading available times…';
   try{const params=new URLSearchParams({service_id:serviceSelect.value,stylist_id:stylistSelect.value,date:day});const result=await S.api(`/api/availability?${params}`);if(request!==latestRequest)return;S.slotButtons($('time-slots'),result.slots,time,value=>{time=value;summary();});}
   catch(error){if(request!==latestRequest)return;$('time-slots').replaceChildren();S.showError('availability-error',error);}
 }
 function goToStep(number){step=number;for(let i=1;i<=3;i++){$(`booking-step-${i}`).hidden=i!==number;$(`progress-${i}`).classList.toggle('active',i<=number);}S.showError('booking-error',null);if(number===2)$('customer-name').focus({preventScroll:true});}
 serviceSelect.addEventListener('change',()=>{goToStep(1);loadSlots();});stylistSelect.addEventListener('change',()=>{goToStep(1);loadSlots();});
 $('continue-booking').addEventListener('click',()=>{if(time&&!saving)goToStep(2);});$('back-to-visit').addEventListener('click',()=>{if(saving)return;goToStep(1);loadSlots();});
 document.querySelectorAll('[data-service]').forEach(card=>card.addEventListener('click',()=>{if(saving)return;serviceSelect.value=card.dataset.service;goToStep(1);loadSlots();$('book').scrollIntoView({behavior:'smooth',block:'start'});}));
 document.querySelectorAll('.filter-chip').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('.filter-chip').forEach(chip=>{chip.classList.toggle('active',chip===button);chip.setAttribute('aria-pressed',String(chip===button));});document.querySelectorAll('.service-card').forEach(card=>card.hidden=button.dataset.category!=='all'&&card.dataset.category!==button.dataset.category);}));
 $('booking-form').addEventListener('submit',async event=>{event.preventDefault();if(!time||!day||saving)return;saving=true;const payload={...Object.fromEntries(new FormData(event.target)),service_id:serviceSelect.value,stylist_id:stylistSelect.value,date:day,time};S.showError('booking-error',null);const button=$('confirm-booking');const controls=[...document.querySelectorAll('[data-service],#service-select,#stylist-select,#booking-form input,#booking-form textarea,#back-to-visit')];controls.forEach(control=>control.disabled=true);S.busy(button,true,'Confirming…');try{const result=await S.api('/api/bookings',payload);const b=result.booking;confirmedReference=b.reference;$('confirmation-reference').textContent=b.reference;$('confirmation-message').textContent=`${b.service_name} with ${b.stylist_name} on ${S.formatDate(b.date)} at ${S.formatTime(b.time)} (Eastern Time).`;$('summary-stylist').textContent=b.stylist_name;goToStep(3);$('booking-workspace').scrollIntoView({behavior:'smooth',block:'center'});}catch(error){S.showError('booking-error',error);}finally{saving=false;controls.forEach(control=>control.disabled=false);S.busy(button,false);}});
 $('copy-reference').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(confirmedReference);$('copy-reference').textContent='Copied';}catch(_){$('copy-reference').textContent='Select and copy the reference above';}});
 $('book-another').addEventListener('click',()=>{$('booking-form').reset();$('copy-reference').textContent='Copy reference';goToStep(1);loadSlots();});
 S.ready.then(()=>{let d=S.date(S.config.min_date);for(let i=0;i<=90;i++){const iso=S.iso(d);if(availableDate(iso)){day=iso;break;}d.setDate(d.getDate()+1);}month=new Date(d.getFullYear(),d.getMonth(),1);renderCalendar();summary();loadSlots();}).catch(error=>S.showError('availability-error',error));
})();
