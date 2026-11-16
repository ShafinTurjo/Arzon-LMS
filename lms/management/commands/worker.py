import time
from pathlib import Path
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Single worker: scheduled tasks every minute and a persisted daily backup schedule.'
    def add_arguments(self, parser): parser.add_argument('--once', action='store_true')
    def handle(self, *args, **options):
        marker = settings.DATA_DIR / '.last-backup-day'
        while True:
            try:
                call_command('run_scheduled')
                today = timezone.localdate().isoformat()
                if not marker.exists() or marker.read_text().strip() != today:
                    call_command('backup')
                    marker.write_text(today)
            except Exception as exc:
                self.stderr.write(f'Worker cycle failed: {type(exc).__name__}. Check service configuration.')
                if options['once']: raise
            if options['once']: return
            time.sleep(60)
