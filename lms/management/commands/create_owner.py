import getpass
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from lms.models import User


class Command(BaseCommand):
    help = 'Interactively create the first owner without storing a default password.'
    def add_arguments(self, parser): parser.add_argument('username')
    def handle(self, *args, **options):
        if User.objects.filter(role='owner').exists(): raise CommandError('An owner already exists. Use the Accounts page.')
        password = getpass.getpass('Owner password: ')
        if password != getpass.getpass('Repeat password: '): raise CommandError('Passwords do not match.')
        user = User(username=options['username'], role='owner')
        try: validate_password(password, user)
        except ValidationError as exc: raise CommandError(' '.join(exc.messages))
        user.set_password(password)
        user.full_clean()
        user.save()
        self.stdout.write('Owner created. Sign in and create staff, student and guardian accounts.')
