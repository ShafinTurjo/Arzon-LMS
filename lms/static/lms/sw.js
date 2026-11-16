// No caching of authenticated pages, student records or uploaded files.
self.addEventListener('push', event => {
  let data = { title: 'Arzon LMS', body: 'You have a new update.' };
  try { data = event.data.json(); } catch (_) {}
  event.waitUntil(self.registration.showNotification(data.title || 'Arzon LMS', { body: data.body || '', tag: data.tag || 'arzon-update' }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(clients.openWindow('/notifications/'));
});
