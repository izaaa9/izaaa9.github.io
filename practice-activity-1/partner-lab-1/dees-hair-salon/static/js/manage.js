/* Serialize appointment lookups and changes so a late response cannot display
   one visit while the private reference/email belong to a different visit. */
(() => {
  'use strict';
  const S = window.Salon;
  const $ = id => document.getElementById(id);
  let booking = null, credentials = null, newTime = '', requestNumber = 0, working = false;

  function lockControls(locked) {
    working = locked;
    document.querySelectorAll('#lookup-form input, #lookup-form button, #manage-result button, #manage-result input, #manage-result select')
      .forEach(control => control.disabled = locked);
    if (!locked) $('save-reschedule').disabled = !newTime;
  }
  function clearNotices() {
    S.showError('manage-error', null);
    $('manage-success').hidden = true;
  }
  function success(message) {
    $('manage-success').textContent = message;
    $('manage-success').hidden = false;
  }
  function render() {
    requestNumber++;
    newTime = '';
    const b = booking;
    $('manage-empty').hidden = true;
    $('booking-found').hidden = false;
    $('manage-service').textContent = b.service_name;
    $('manage-status').textContent = b.status === 'cancelled' ? 'Cancelled' : 'Scheduled';
    $('manage-status').classList.toggle('cancelled', b.status === 'cancelled');
    const dl = $('manage-details');
    dl.replaceChildren();
    [
      ['Reference', b.reference], ['Guest', b.name], ['Date', S.formatDate(b.date)],
      ['Time', `${S.formatTime(b.time)} – ${S.formatTime(b.end_time)} ET`],
      ['Stylist', b.stylist_name], ['Service total', S.money(b.price)]
    ].forEach(([label, value]) => {
      const row = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = label;
      dd.textContent = value;
      row.append(dt, dd);
      dl.append(row);
    });
    $('manage-actions').hidden = b.status === 'cancelled';
    $('reschedule-panel').hidden = true;
    $('cancel-confirmation').hidden = true;
  }
  $('lookup-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (working) return;
    const fields = Object.fromEntries(new FormData(event.target));
    const button = event.target.querySelector('button');
    requestNumber++;
    newTime = '';
    booking = credentials = null;
    $('booking-found').hidden = true;
    $('manage-empty').hidden = false;
    S.showError('lookup-error', null);
    clearNotices();
    lockControls(true);
    S.busy(button, true, 'Finding your visit…');
    try {
      await S.ready;
      const result = await S.api('/api/bookings/lookup', fields);
      credentials = {reference: fields.reference.trim(), email: fields.email.trim()};
      booking = result.booking;
      render();
    } catch (error) {
      S.showError('lookup-error', error);
    } finally {
      S.busy(button, false);
      lockControls(false);
    }
  });
  $('open-cancel').onclick = () => {
    if (working) return;
    requestNumber++;
    clearNotices();
    $('cancel-confirmation').hidden = false;
    $('reschedule-panel').hidden = true;
  };
  $('keep-appointment').onclick = () => { $('cancel-confirmation').hidden = true; };
  $('confirm-cancel').onclick = async () => {
    if (working || !credentials) return;
    const payload = {...credentials}, button = $('confirm-cancel');
    requestNumber++;
    clearNotices();
    lockControls(true);
    S.busy(button, true, 'Cancelling…');
    try {
      const result = await S.api('/api/bookings/cancel', payload);
      booking = result.booking;
      render();
      success('Your appointment has been cancelled. You can book a fresh visit whenever you’re ready.');
    } catch (error) {
      S.showError('manage-error', error);
    } finally {
      S.busy(button, false);
      lockControls(false);
    }
  };
  async function loadTimes() {
    if (working || !credentials) return;
    newTime = '';
    $('save-reschedule').disabled = true;
    const version = ++requestNumber;
    const payload = {...credentials, date: $('reschedule-date').value, stylist_id: $('reschedule-stylist').value};
    clearNotices();
    $('reschedule-slots').textContent = 'Loading available times…';
    try {
      const result = await S.api('/api/bookings/availability', payload);
      if (version !== requestNumber) return;
      S.slotButtons($('reschedule-slots'), result.slots, newTime, value => {
        if (working) return;
        newTime = value;
        $('save-reschedule').disabled = false;
      });
    } catch (error) {
      if (version !== requestNumber) return;
      $('reschedule-slots').replaceChildren();
      S.showError('manage-error', error);
    }
  }
  $('open-reschedule').onclick = () => {
    if (working) return;
    clearNotices();
    $('reschedule-panel').hidden = false;
    $('cancel-confirmation').hidden = true;
    $('reschedule-date').min = S.config.min_date;
    $('reschedule-date').max = S.config.max_date;
    $('reschedule-date').value = booking.date;
    $('reschedule-stylist').value = booking.stylist_id;
    loadTimes();
  };
  $('close-reschedule').onclick = () => { requestNumber++; $('reschedule-panel').hidden = true; };
  $('reschedule-date').addEventListener('change', loadTimes);
  $('reschedule-stylist').addEventListener('change', loadTimes);
  $('save-reschedule').onclick = async () => {
    if (working || !newTime || !credentials) return;
    const payload = {...credentials, date: $('reschedule-date').value, time: newTime, stylist_id: $('reschedule-stylist').value};
    const button = $('save-reschedule');
    requestNumber++;
    clearNotices();
    lockControls(true);
    S.busy(button, true, 'Saving…');
    try {
      const result = await S.api('/api/bookings/reschedule', payload);
      booking = result.booking;
      render();
      success('Your appointment has been updated. Your booking reference stays the same.');
    } catch (error) {
      S.showError('manage-error', error);
    } finally {
      S.busy(button, false);
      lockControls(false);
    }
  };
})();
