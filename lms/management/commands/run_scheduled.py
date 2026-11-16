from datetime import timedelta
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from lms.models import Exam, Attempt
from lms.services import notify_batch, finalize_attempt


class Command(BaseCommand):
    help = 'Send 24-hour exam reminders, finalize timed-out attempts, and dispatch delivery jobs.'
    def handle(self, *args, **options):
        now = timezone.now()
        for exam in Exam.objects.filter(published=True, starts_at__gt=now, starts_at__lte=now + timedelta(hours=24)):
            with transaction.atomic():
                notify_batch(exam.batch, 'Exam reminder', f'{exam.title} starts {timezone.localtime(exam.starts_at):%d %b %Y %H:%M}. Duration: {exam.duration_minutes} minutes.', f'exam-reminder:{exam.pk}:{exam.starts_at.isoformat()}')
        for pk in Attempt.objects.filter(submitted_at=None, deadline__lte=now).values_list('pk', flat=True):
            with transaction.atomic():
                attempt = Attempt.objects.select_for_update().get(pk=pk)
                finalize_attempt(attempt, now)
                attempt.save()
        call_command('dispatch_notifications')
