import json
from datetime import timedelta
from urllib.request import Request, urlopen
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q, F
from django.utils import timezone
from lms.models import Delivery, PushSubscription


class Command(BaseCommand):
    help = 'Deliver pending SMS and browser push messages. Safe to run every minute; providers must honor the idempotency key.'
    def handle(self, *args, **options):
        now = timezone.now()
        Delivery.objects.filter(state='sending', updated_at__lt=now - timedelta(minutes=5)).update(state='failed', last_error='Worker lease expired')
        queue = Delivery.objects.filter(Q(state='pending') | Q(state='failed', updated_at__lt=now - timedelta(minutes=5)), attempts__lt=5).order_by('pk')[:100]
        delivered = 0
        for delivery in queue:
            configured = settings.SMS_WEBHOOK_URL and settings.SMS_WEBHOOK_TOKEN if delivery.channel == 'sms' else settings.VAPID_PRIVATE_KEY
            if not configured: continue  # Never claim delivery success without provider configuration.
            claimed = Delivery.objects.filter(pk=delivery.pk, state=delivery.state, attempts=delivery.attempts).update(state='sending', attempts=F('attempts') + 1, updated_at=now)
            if not claimed: continue
            try:
                if delivery.channel == 'sms':
                    if not settings.SMS_WEBHOOK_URL.startswith('https://'): raise ValueError('SMS webhook must use HTTPS')
                    payload = json.dumps({'to': delivery.phone, 'message': delivery.body, 'idempotency_key': delivery.dedupe_key}).encode()
                    req = Request(settings.SMS_WEBHOOK_URL, data=payload, headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {settings.SMS_WEBHOOK_TOKEN}', 'Idempotency-Key': delivery.dedupe_key})
                    with urlopen(req, timeout=15) as response:
                        result = json.loads(response.read(65536))
                    if result.get('accepted') is not True: raise ValueError('SMS provider did not confirm acceptance')
                else:
                    from pywebpush import webpush, WebPushException
                    subscriptions = list(PushSubscription.objects.filter(user=delivery.user))
                    if not subscriptions: raise ValueError('No browser subscription for recipient')
                    for sub in subscriptions:
                        try:
                            webpush(subscription_info={'endpoint': sub.endpoint, 'keys': sub.keys}, data=json.dumps({'title': delivery.title, 'body': delivery.body, 'tag': delivery.dedupe_key}), vapid_private_key=settings.VAPID_PRIVATE_KEY, vapid_claims={'sub': settings.VAPID_SUBJECT}, timeout=15)
                        except WebPushException as exc:
                            if exc.response is not None and exc.response.status_code in (404, 410): sub.delete()
                            raise
                Delivery.objects.filter(pk=delivery.pk).update(state='sent', last_error='', updated_at=timezone.now())
                delivered += 1
            except Exception as exc:
                # Avoid persisting provider payloads, auth headers or keys in logs.
                error = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                Delivery.objects.filter(pk=delivery.pk).update(state='failed', last_error=error[:250], updated_at=timezone.now())
        self.stdout.write(f'{delivered} delivery job(s) accepted by providers.')
