'use strict';
const menu = document.querySelector('#menu-toggle');
menu?.addEventListener('click', () => {
  const open = document.querySelector('#sidebar').classList.toggle('open');
  menu.setAttribute('aria-expanded', String(open));
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') { document.querySelector('#sidebar')?.classList.remove('open'); menu?.setAttribute('aria-expanded', 'false'); }
});
const examForm = document.querySelector('#exam-form');
const timer = document.querySelector('#exam-timer');
if (examForm && timer) {
  const deadline = new Date(timer.dataset.deadline).getTime();
  const status = document.querySelector('#save-status');
  let pending = Promise.resolve();
  let expired = false;
  const save = () => {
    pending = pending.then(async () => {
      if (expired) return;
      status.textContent = 'Saving answers…';
      try {
        const data = new FormData(examForm); data.set('action', 'save');
        const response = await fetch(location.href, { method: 'POST', body: data, headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        if (!response.ok) throw new Error('Save failed');
        const result = await response.json();
        status.textContent = result.submitted ? 'Exam submitted.' : `Saved at ${new Date().toLocaleTimeString()}`;
      } catch { status.textContent = 'Not saved. Check your connection and use Save answers before the deadline.'; }
    });
  };
  examForm.querySelectorAll('input[type=radio]').forEach(input => input.addEventListener('change', save));
  const tick = () => {
    const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    timer.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')} remaining`;
    if (seconds === 0 && !expired) {
      expired = true;
      status.textContent = 'Time is up. Submitting your saved answers…';
      pending.finally(() => examForm.requestSubmit(examForm.querySelector('[value=submit]')));
    }
  };
  tick(); setInterval(tick, 1000);
  window.addEventListener('offline', () => { status.textContent = 'You are offline. Reconnect before the deadline to save your answers.'; });
}
const pushButton = document.querySelector('#enable-push');
pushButton?.addEventListener('click', async () => {
  const status = document.querySelector('#push-status');
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) throw new Error('This browser does not support push notifications.');
    if (await Notification.requestPermission() !== 'granted') throw new Error('Notification permission was not granted.');
    const registration = await navigator.serviceWorker.register('/sw.js');
    await navigator.serviceWorker.ready;
    const base64 = pushButton.dataset.key.replace(/-/g, '+').replace(/_/g, '/');
    const key = Uint8Array.from(atob(base64 + '='.repeat((4 - base64.length % 4) % 4)), c => c.charCodeAt(0));
    const subscription = await registration.pushManager.getSubscription() || await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    const response = await fetch('/push/subscribe/', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value }, body: JSON.stringify(subscription) });
    if (!response.ok) throw new Error('Could not save your subscription.');
    status.textContent = 'Browser notifications enabled.';
  } catch (error) { status.textContent = error.message; }
});
