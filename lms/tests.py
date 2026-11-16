import io
import json
import tarfile
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from .models import *
from .access import scoped
from .forms import record_form, validate_record
from .services import mark_attendance, start_attempt, save_answers, grade


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class LMSTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user('owner', password='Valid-long-pass!83', role='owner')
        cls.admin = User.objects.create_user('admin', role='admin')
        cls.teacher = User.objects.create_user('teacher', role='teacher')
        cls.other_teacher = User.objects.create_user('otherteacher', role='teacher')
        cls.accountant = User.objects.create_user('accountant', role='accountant')
        cls.guardian = User.objects.create_user('guardian', role='guardian')
        cls.student_user = User.objects.create_user('student', role='student')
        cls.other_user = User.objects.create_user('otherstudent', role='student')
        cls.course = Course.objects.create(name='Science')
        cls.subject = Subject.objects.create(course=cls.course, name='Math')
        cls.batch = Batch.objects.create(name='Batch A', course=cls.course)
        cls.other_batch = Batch.objects.create(name='Batch B', course=cls.course)
        cls.batch.teachers.add(cls.teacher)
        cls.other_batch.teachers.add(cls.other_teacher)
        cls.student = Student.objects.create(user=cls.student_user, admission_no='001', batch=cls.batch, guardian_name='Parent', guardian_phone='+8801000000000')
        cls.student.guardians.add(cls.guardian)
        cls.other_student = Student.objects.create(user=cls.other_user, admission_no='002', batch=cls.other_batch, guardian_name='Other parent', guardian_phone='+8801000000001')
        now = timezone.now()
        cls.session = ClassSession.objects.create(batch=cls.batch, subject=cls.subject, teacher=cls.teacher, starts_at=now-timedelta(hours=2), ends_at=now-timedelta(hours=1))
        cls.exam = Exam.objects.create(title='Algebra', batch=cls.batch, subject=cls.subject, kind='mcq', starts_at=now-timedelta(hours=1), ends_at=now+timedelta(hours=2), duration_minutes=20, total_marks=10, published=True)
        cls.question = Question.objects.create(exam=cls.exam, text='2 + 2?', option_a='3', option_b='4', option_c='5', option_d='6', correct_option='b')
        cls.assignment = Assignment.objects.create(title='Homework', batch=cls.batch, subject=cls.subject, deadline=now+timedelta(days=1), total_marks=10, instructions='Solve')
        cls.invoice = Invoice.objects.create(student=cls.student, title='Tuition', amount=1000, due_date=timezone.localdate())

    def login(self, user): self.client.force_login(user)
    def pdf(self): return SimpleUploadedFile('answer.pdf', b'%PDF-1.4\nTest answer', content_type='application/pdf')

    def test_anonymous_redirected(self):
        for url in ['/', '/records/students/', '/results/', '/history/']:
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_all_role_dashboards_render(self):
        for user in [self.owner, self.admin, self.teacher, self.accountant, self.guardian, self.student_user]:
            self.login(user)
            self.assertEqual(self.client.get('/').status_code, 200, user.role)

    def test_owner_pages_render(self):
        self.login(self.owner)
        for url in ['/records/students/', '/records/courses/', '/records/subjects/', '/records/batches/', '/records/routine/', '/records/materials/', '/records/exams/', '/records/questions/', '/records/assignments/', '/records/invoices/', '/records/payments/', '/records/notices/', '/accounts/', '/attendance/', '/results/', '/doubts/', '/notifications/', '/history/', '/deliveries/', f'/students/{self.student.pk}/', f'/exams/{self.exam.pk}/', f'/assignments/{self.assignment.pk}/']:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_create_forms_render(self):
        self.login(self.owner)
        from .forms import REGISTRY
        for kind in REGISTRY:
            self.assertEqual(self.client.get(f'/records/{kind}/new/').status_code, 200, kind)
        self.assertEqual(self.client.get('/accounts/new/').status_code, 200)

    def test_student_cannot_edit_or_view_answer_key(self):
        self.login(self.student_user)
        self.assertEqual(self.client.get('/records/questions/').status_code, 403)
        self.assertEqual(self.client.post('/records/students/new/', {}).status_code, 403)
        self.assertEqual(self.client.get('/accounts/').status_code, 403)
        self.assertEqual(self.client.get('/history/').status_code, 403)

    def test_teacher_batch_isolation(self):
        self.login(self.other_teacher)
        self.assertEqual(self.client.get(f'/exams/{self.exam.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/attendance/{self.session.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/students/{self.student.pk}/').status_code, 404)

    def test_guardian_only_own_children(self):
        self.login(self.guardian)
        self.assertEqual(self.client.get(f'/students/{self.student.pk}/').status_code, 200)
        self.assertEqual(self.client.get(f'/students/{self.other_student.pk}/').status_code, 404)
        self.other_student.guardians.add(self.guardian)
        response = self.client.get('/')
        self.assertContains(response, '001')
        self.assertContains(response, '002')

    def test_accountant_cannot_see_academic_records(self):
        self.assertFalse(scoped(Exam, self.accountant).exists())
        self.assertFalse(scoped(Material, self.accountant).exists())
        self.login(self.accountant)
        self.assertEqual(self.client.post(f'/grade/exam/1/', {'score': 10}).status_code, 403)

    def test_teacher_cannot_see_finance(self):
        self.login(self.teacher)
        self.assertEqual(self.client.get('/records/payments/').status_code, 403)
        self.assertFalse(scoped(Invoice, self.teacher).exists())

    def test_absence_creates_deduplicated_guardian_notifications(self):
        mark_attendance(self.teacher, self.session, {str(self.student.pk): 'absent'})
        mark_attendance(self.teacher, self.session, {str(self.student.pk): 'absent'})
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.guardian).count(), 1)
        self.assertEqual(Delivery.objects.filter(channel='sms').count(), 1)
        self.assertEqual(AuditEvent.objects.filter(entity='attendance').count(), 2)

    def test_attendance_validated_atomically(self):
        with self.assertRaises(ValidationError): mark_attendance(self.teacher, self.session, {str(self.student.pk): 'invalid'})
        self.assertEqual(Attendance.objects.count(), 0)
        with self.assertRaises(PermissionDenied): mark_attendance(self.other_teacher, self.session, {str(self.student.pk): 'present'})

    def test_future_cancelled_attendance_rejected(self):
        self.session.status = 'cancelled'
        with self.assertRaises(ValidationError): mark_attendance(self.teacher, self.session, {str(self.student.pk): 'present'})
        self.session.status = 'scheduled'; self.session.starts_at = timezone.now() + timedelta(days=1)
        with self.assertRaises(ValidationError): mark_attendance(self.teacher, self.session, {str(self.student.pk): 'present'})

    def test_mcq_timer_autograding_and_single_attempt(self):
        attempt = start_attempt(self.student_user, self.exam)
        self.assertEqual(start_attempt(self.student_user, self.exam).pk, attempt.pk)
        self.assertLessEqual(attempt.deadline, self.exam.ends_at)
        self.login(self.student_user)
        response = self.client.get(f'/attempts/{attempt.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'correct_option')
        result = save_answers(self.student_user, attempt.pk, {str(self.question.pk): 'b'}, submit=True)
        self.assertEqual(result.score, Decimal('10.00'))
        save_answers(self.student_user, attempt.pk, {str(self.question.pk): 'a'}, submit=True)
        result.refresh_from_db(); self.assertEqual(result.score, Decimal('10.00'))

    def test_expired_attempt_uses_only_previously_saved_answers(self):
        attempt = start_attempt(self.student_user, self.exam)
        save_answers(self.student_user, attempt.pk, {str(self.question.pk): 'a'})
        Attempt.objects.filter(pk=attempt.pk).update(deadline=timezone.now()-timedelta(seconds=1))
        result = save_answers(self.student_user, attempt.pk, {str(self.question.pk): 'b'}, submit=True)
        self.assertEqual(result.score, 0)

    def test_scheduler_finalizes_abandoned_exam(self):
        attempt = start_attempt(self.student_user, self.exam)
        save_answers(self.student_user, attempt.pk, {str(self.question.pk): 'b'})
        Attempt.objects.filter(pk=attempt.pk).update(deadline=timezone.now()-timedelta(seconds=1))
        call_command('run_scheduled', stdout=io.StringIO())
        attempt.refresh_from_db()
        self.assertIsNotNone(attempt.submitted_at)
        self.assertEqual(attempt.score, 10)

    def test_results_hidden_until_published(self):
        attempt = start_attempt(self.student_user, self.exam)
        save_answers(self.student_user, attempt.pk, {str(self.question.pk): 'b'}, submit=True)
        self.assertFalse(scoped(Attempt, self.student_user).exists())
        self.exam.results_published = True; self.exam.save()
        self.assertTrue(scoped(Attempt, self.student_user).exists())
        self.login(self.student_user)
        self.assertEqual(self.client.get(f'/results/{self.exam.pk}/merit/').status_code, 200)

    def test_unauthorized_exam_attempt_rejected(self):
        with self.assertRaises(PermissionDenied): start_attempt(self.other_user, self.exam)
        attempt = start_attempt(self.student_user, self.exam)
        with self.assertRaises(PermissionDenied): save_answers(self.other_user, attempt.pk, {})

    def test_inactive_student_cannot_start_exam(self):
        self.student.active = False; self.student.save()
        with self.assertRaises(PermissionDenied): start_attempt(self.student_user, self.exam)

    def test_exam_questions_locked_after_attempt(self):
        start_attempt(self.student_user, self.exam)
        form = record_form('questions', self.teacher, {'exam': self.exam.pk, 'text': 'Changed?', 'option_a': 'a', 'option_b': 'b', 'option_c': 'c', 'option_d': 'd', 'correct_option': 'a', 'marks': 1}, instance=self.question)
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(ValidationError): validate_record(form)

    def test_written_exam_upload_and_mark_history(self):
        self.exam.kind = 'written'; self.exam.save()
        attempt = start_attempt(self.student_user, self.exam)
        result = save_answers(self.student_user, attempt.pk, {}, submit=True, file=self.pdf())
        self.assertTrue(result.file)
        grade(self.teacher, Attempt, attempt.pk, Decimal('7'), 'Good')
        grade(self.teacher, Attempt, attempt.pk, Decimal('8'), 'Correction')
        event = AuditEvent.objects.filter(action='grade').first()
        self.assertEqual(event.before['score'], '7.00')
        self.assertEqual(event.after['score'], '8')
        with self.assertRaises(ValidationError): grade(self.teacher, Attempt, attempt.pk, Decimal('11'), '')

    def test_assignment_upload_deadline_and_download_access(self):
        self.login(self.student_user)
        response = self.client.post(f'/assignments/{self.assignment.pk}/', {'file': self.pdf(), 'note': 'Done'})
        self.assertEqual(response.status_code, 302)
        submission = Submission.objects.get()
        self.assertEqual(self.client.get(f'/download/submission/{submission.pk}/').status_code, 200)
        self.login(self.other_user)
        self.assertEqual(self.client.get(f'/download/submission/{submission.pk}/').status_code, 404)
        self.login(self.student_user)
        self.assignment.deadline = timezone.now()-timedelta(hours=1); self.assignment.save()
        response = self.client.post(f'/assignments/{self.assignment.pk}/', {'file': self.pdf()})
        self.assertContains(response, 'deadline has passed')
        self.assertEqual(Submission.objects.count(), 1)

    def test_material_access_and_upload_validation(self):
        material = Material.objects.create(title='Private notes', course=self.course, batch=self.batch, kind='pdf', file=self.pdf())
        self.login(self.other_user)
        self.assertEqual(self.client.get(f'/download/material/{material.pk}/').status_code, 404)
        self.login(self.student_user)
        self.assertEqual(self.client.get(f'/download/material/{material.pk}/').status_code, 200)
        with self.assertRaises(ValidationError): validate_upload(SimpleUploadedFile('fake.pdf', b'<script>alert(1)</script>'))
        with self.assertRaises(ValidationError): validate_upload(SimpleUploadedFile('attack.html', b'%PDF-1.4'))

    def test_teacher_cannot_publish_course_wide_material(self):
        form = record_form('materials', self.teacher, {'title': 'Notes', 'course': self.course.pk, 'kind': 'live', 'url': 'https://example.com/class'})
        self.assertFalse(form.is_valid())
        self.assertIn('batch', form.errors)

    def test_payment_balance_validation_and_history(self):
        self.login(self.accountant)
        response = self.client.post('/records/payments/new/', {'invoice': self.invoice.pk, 'amount': '600', 'method': 'cash', 'reference': 'R001'})
        self.assertEqual(response.status_code, 302)
        payment = Payment.objects.get()
        self.assertEqual(self.invoice.balance, 400)
        response = self.client.post('/records/payments/new/', {'invoice': self.invoice.pk, 'amount': '500', 'method': 'cash', 'reference': 'R002'})
        self.assertContains(response, 'exceeds the outstanding')
        self.assertEqual(Payment.objects.count(), 1)
        response = self.client.post(f'/records/payments/{payment.pk}/edit/', {'invoice': self.invoice.pk, 'amount': '550', 'method': 'cash', 'reference': 'R001'})
        self.assertEqual(response.status_code, 302)
        event = AuditEvent.objects.filter(entity='payment').first()
        self.assertEqual(event.before['amount'], '600.00')
        self.assertEqual(self.invoice.balance, 450)

    def test_batch_transfer_history(self):
        self.login(self.owner)
        response = self.client.post(f'/records/students/{self.student.pk}/edit/', {'user': self.student_user.pk, 'admission_no': '001', 'admitted_on': timezone.localdate().isoformat(), 'batch': self.other_batch.pk, 'guardian_name': 'Parent', 'guardian_phone': '+8801000000000', 'active': 'on', 'guardians': [self.guardian.pk]})
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.batch, self.other_batch)
        self.assertEqual(BatchTransfer.objects.get().from_batch, self.batch)

    def test_schedule_overlap_validation(self):
        conflict = ClassSession(batch=self.batch, subject=self.subject, teacher=self.teacher, starts_at=self.session.starts_at+timedelta(minutes=15), ends_at=self.session.ends_at+timedelta(minutes=15))
        with self.assertRaises(ValidationError): conflict.full_clean()

    def test_cancellation_notifies_guardian(self):
        self.login(self.teacher)
        response = self.client.post(f'/records/routine/{self.session.pk}/edit/', {'batch': self.batch.pk, 'subject': self.subject.pk, 'teacher': self.teacher.pk, 'starts_at': timezone.localtime(self.session.starts_at).strftime('%Y-%m-%dT%H:%M'), 'ends_at': timezone.localtime(self.session.ends_at).strftime('%Y-%m-%dT%H:%M'), 'status': 'cancelled', 'note': 'Teacher unavailable'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Notification.objects.filter(user=self.guardian, title='Class routine updated').exists())

    def test_admin_cannot_create_owner(self):
        self.login(self.admin)
        response = self.client.post('/accounts/new/', {'username': 'intruder', 'role': 'owner', 'password': 'Safe-test-password!92', 'is_active': 'on'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='intruder').exists())
        self.assertEqual(self.client.get(f'/accounts/{self.owner.pk}/edit/').status_code, 403)

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post('/records/courses/new/', {'name': 'Forged'}).status_code, 403)

    def test_login_throttled(self):
        for _ in range(10): self.client.post('/login/', {'username': 'owner', 'password': 'bad'})
        self.assertEqual(self.client.post('/login/', {'username': 'owner', 'password': 'bad'}).status_code, 429)

    def test_push_rejects_internal_endpoints(self):
        self.login(self.student_user)
        response = self.client.post('/push/subscribe/', json.dumps({'endpoint': 'https://127.0.0.1/private', 'keys': {'auth': 'a'*20, 'p256dh': 'b'*20}}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(PushSubscription.objects.count(), 0)

    @override_settings(SMS_WEBHOOK_URL='', SMS_WEBHOOK_TOKEN='', VAPID_PRIVATE_KEY='')
    def test_unconfigured_messages_never_marked_sent(self):
        mark_attendance(self.teacher, self.session, {str(self.student.pk): 'absent'})
        call_command('dispatch_notifications', stdout=io.StringIO())
        self.assertFalse(Delivery.objects.filter(state='sent').exists())
        self.assertTrue(Delivery.objects.filter(state='pending').exists())

    @override_settings(SMS_WEBHOOK_URL='https://sms.example.com/send', SMS_WEBHOOK_TOKEN='test')
    @patch('lms.management.commands.dispatch_notifications.urlopen')
    def test_sms_worker_provider_acknowledgement(self, mocked):
        Delivery.objects.create(channel='sms', phone='+8801000000000', title='Test', body='Body', dedupe_key='test-sms')
        mocked.return_value.__enter__.return_value.read.return_value = b'{"accepted": true}'
        call_command('dispatch_notifications', stdout=io.StringIO())
        self.assertEqual(Delivery.objects.get().state, 'sent')
        call_command('dispatch_notifications', stdout=io.StringIO())
        self.assertEqual(mocked.call_count, 1)

    def test_exam_reminder_deduplication(self):
        self.exam.starts_at = timezone.now()+timedelta(hours=12); self.exam.ends_at = timezone.now()+timedelta(hours=13); self.exam.save()
        call_command('run_scheduled', stdout=io.StringIO())
        call_command('run_scheduled', stdout=io.StringIO())
        self.assertEqual(Notification.objects.filter(user=self.guardian, title='Exam reminder').count(), 1)

    def test_doubt_answer_notification(self):
        self.login(self.student_user)
        response = self.client.post('/doubts/', {'subject': self.subject.pk, 'question': 'How do I solve this?'})
        self.assertEqual(response.status_code, 302)
        doubt = Doubt.objects.get()
        self.login(self.other_teacher)
        self.assertEqual(self.client.post(f'/doubts/{doubt.pk}/answer/', {'answer': 'Unauthorized'}).status_code, 404)
        self.login(self.teacher)
        self.assertEqual(self.client.post(f'/doubts/{doubt.pk}/answer/', {'answer': 'Start by factoring.'}).status_code, 302)
        self.assertTrue(Notification.objects.filter(user=self.student_user).exists())

    def test_teacher_cannot_edit_shared_course_material(self):
        material = Material.objects.create(title='Shared notes', course=self.course, kind='live', url='https://example.com/class')
        self.login(self.teacher)
        self.assertEqual(self.client.get(f'/records/materials/{material.pk}/edit/').status_code, 404)
        self.assertTrue(scoped(Material, self.teacher).filter(pk=material.pk).exists())

    def test_late_first_assignment_submission_rejected(self):
        self.assignment.deadline = timezone.now()-timedelta(minutes=1)
        self.assignment.save()
        self.login(self.student_user)
        response = self.client.post(f'/assignments/{self.assignment.pk}/', {'file': self.pdf()})
        self.assertContains(response, 'deadline has passed')
        self.assertFalse(Submission.objects.exists())

    def test_malformed_push_payload_is_rejected(self):
        self.login(self.student_user)
        for data in [[], {'endpoint': 123, 'keys': {}}, {'endpoint': 'https://fcm.googleapis.com/push', 'keys': None}]:
            response = self.client.post('/push/subscribe/', json.dumps(data), content_type='application/json')
            self.assertEqual(response.status_code, 400)

    def test_inactive_batch_hides_learning_materials(self):
        material = Material.objects.create(title='Lesson', course=self.course, batch=self.batch, kind='live', url='https://example.com/class')
        self.batch.active = False; self.batch.save()
        self.assertFalse(scoped(Material, self.student_user).filter(pk=material.pk).exists())
