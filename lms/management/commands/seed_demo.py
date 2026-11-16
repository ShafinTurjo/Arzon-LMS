import secrets
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from lms.models import *


class Command(BaseCommand):
    help = 'Add fictional demo data locally. Generates random passwords; never runs automatically.'
    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG: raise CommandError('Demo data is only allowed with DEBUG=1.')
        if User.objects.exists(): raise CommandError('Use an empty database for the demo.')
        users = {}
        for role, first, last in [('owner', 'Shafin', 'Rahman'), ('teacher', 'Nadia', 'Ahmed'), ('student', 'Ayaan', 'Rahman'), ('guardian', 'Farhana', 'Rahman'), ('accountant', 'Rafi', 'Hasan'), ('admin', 'Sara', 'Khan')]:
            password = secrets.token_urlsafe(14)
            user = User.objects.create_user(f'demo_{role}', password=password, role=role, first_name=first, last_name=last)
            users[role] = user
            self.stdout.write(f'{user.username}: {password}')
        course = Course.objects.create(name='HSC 2027 · Science', description='A strong foundation for your next chapter.')
        math = Subject.objects.create(course=course, name='Higher Mathematics')
        physics = Subject.objects.create(course=course, name='Physics')
        batch = Batch.objects.create(name='HSC Science · A', course=course, shift='Morning')
        batch.teachers.add(users['teacher'])
        student = Student.objects.create(user=users['student'], admission_no='ARZ-2026-001', batch=batch, guardian_name='Farhana Rahman', guardian_phone='+8801000000000', address='Fictional demo profile')
        student.subjects.add(math, physics)
        student.guardians.add(users['guardian'])
        sibling_user = User.objects.create_user('demo_sibling', role='student', first_name='Anika', last_name='Rahman')
        sibling_user.set_unusable_password(); sibling_user.save()
        sibling = Student.objects.create(user=sibling_user, admission_no='ARZ-2026-002', batch=batch, guardian_name='Farhana Rahman', guardian_phone='+8801000000000')
        sibling.guardians.add(users['guardian']); sibling.subjects.add(math, physics)
        now = timezone.now()
        for index, name in enumerate(['Zara', 'Ishaan', 'Meher', 'Arif', 'Nusrat', 'Rayhan']):
            user = User.objects.create_user(f'demo_learner_{index}', role='student', first_name=name)
            user.set_unusable_password(); user.save()
            Student.objects.create(user=user, admission_no=f'ARZ-2026-{index+3:03}', batch=batch, guardian_name='Demo Guardian', guardian_phone='+8801000000000')
        for index in range(4):
            start = now + timedelta(hours=2 + index * 24)
            ClassSession.objects.create(batch=batch, subject=math if index % 2 == 0 else physics, teacher=users['teacher'], starts_at=start, ends_at=start + timedelta(hours=1), room='Room 203')
        past = ClassSession.objects.create(batch=batch, subject=math, teacher=users['teacher'], starts_at=now - timedelta(days=1), ends_at=now - timedelta(days=1, hours=-1))
        for index, s in enumerate(Student.objects.all()):
            Attendance.objects.create(session=past, student=s, status='absent' if index == 6 else 'present', marked_by=users['teacher'])
            invoice = Invoice.objects.create(student=s, title='November tuition', amount=2500, due_date=timezone.localdate() + timedelta(days=7))
            if index < 6: Payment.objects.create(invoice=invoice, amount=2000, method='cash', reference=f'DEMO-{index+1:04}', received_by=users['accountant'])
        exam = Exam.objects.create(title='Weekly practice · Algebra', batch=batch, subject=math, kind='mcq', starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=1), duration_minutes=20, total_marks=10, published=True, instructions='Choose one answer for each question. Each question has equal weight.')
        for question, options, correct in [('If 2x + 4 = 10, what is x?', ['2', '3', '4', '5'], 'b'), ('What is the square of 12?', ['124', '132', '144', '148'], 'c')]:
            Question.objects.create(exam=exam, text=question, option_a=options[0], option_b=options[1], option_c=options[2], option_d=options[3], correct_option=correct)
        Assignment.objects.create(title='Motion in one dimension', batch=batch, subject=physics, instructions='Solve five problems on velocity and acceleration. Upload your working as a PDF.', deadline=now + timedelta(days=5), total_marks=20)
        Notice.objects.create(title='A fresh week of learning starts here', batch=batch, body='Your updated class routine is ready. Check your upcoming classes and bring your notebook. See you in class!')
        Notice.objects.create(title='Weekly practice exam is now open', batch=batch, body='Take the algebra practice exam before the deadline. Your timer starts when you begin.')
        self.stdout.write('Fictional demo workspace created. Do not connect real SMS credentials to this demo database.')
