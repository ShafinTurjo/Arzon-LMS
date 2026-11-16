import os
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Create an online SQLite snapshot plus private uploads; retain the newest 14 archives.'
    def add_arguments(self, parser): parser.add_argument('--directory', default=os.getenv('BACKUP_DIR', str(settings.DATA_DIR / 'backups')))
    def handle(self, *args, **options):
        target = Path(options['directory'])
        target.mkdir(parents=True, exist_ok=True)
        os.chmod(target, 0o700)
        name = target / f'arzon-{timezone.now():%Y%m%d-%H%M%S-%f}.tar.gz'
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / 'db.sqlite3'
            with sqlite3.connect(settings.DATABASES['default']['NAME']) as source:
                with sqlite3.connect(snapshot) as destination: source.backup(destination)
            with tarfile.open(name, 'w:gz') as archive:
                archive.add(snapshot, arcname='db.sqlite3')
                if settings.MEDIA_ROOT.exists(): archive.add(settings.MEDIA_ROOT, arcname='private_uploads')
        os.chmod(name, 0o600)
        for old in sorted(target.glob('arzon-*.tar.gz'))[:-14]: old.unlink()
        self.stdout.write(str(name))
