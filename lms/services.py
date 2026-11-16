from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.forms.models import model_to_dict
from django.utils import timezone
from .models import *
from .access import *


def snapshot(obj):
    if not obj or not obj.pk: return {}
    data = {}
    for field in obj._meta.concrete_fields:
        if field.name in {'password', 'file'}: continue
        value = getattr(obj, field.attname)
        data[field.name] = str(value) if value is not None else None
    for name in ({Student: ['subjects', 'guardians'], Batch: ['teachers']}.get(type(obj), [])):
        data[name] = list(getattr(obj, name).order_by('pk').values_list('pk', flat=True))
    return data


def audit(user, obj, before, action='update'):
    AuditEvent.objects.create(actor=user, entity=obj._meta.model_name, object_id=obj.pk, action=action, before=before, after=snapshot(obj))


def notify(user, title, body, key):
    Notification.objects.get_or_create(dedupe_key=f'{key}:u{user.pk}', defaults={'user': user, 'title': title, 'body': body})
    Delivery.objects.get_or_create(dedupe_key=f'{key}:push{user.pk}', defaults={'channel': 'push', 'user': user, 'title': title, 'body': body})


def sms(phone, title, body, key):
    if phone:
        Delivery.objects.get_or_create(dedupe_key=f'{key}:sms:{phone}', defaults={'channel': 'sms', 'phone': phone, 'title': title, 'body': body})


def notify_student(student, title, body, key, include_student=True):
    if include_student: notify(student.user, title, body, key)
    for guardian in student.guardians.filter(is_active=True): notify(guardian, title, body, key)
    sms(student.guardian_phone, title, body, key)


def notify_batch(batch, title, body, key):
    for student in Student.objects.filter(batch=batch, active=True).select_related('user').prefetch_related('guardians'):
        notify_student(student, title, body, key)


@transaction.atomic
def mark_attendance(user, session, statuses):
    require(user, ACADEMIC)
    if not scoped(ClassSession, user).filter(pk=session.pk).exists(): raise PermissionDenied
    if session.status == 'cancelled': raise ValidationError('Cannot mark attendance for a cancelled class.')
    if session.starts_at > timezone.now(): raise ValidationError('Attendance opens when the class starts.')
    students = list(Student.objects.filter(batch=session.batch, active=True))
    for student in students:
        status = statuses.get(str(student.pk))
        if status not in {'present', 'late', 'absent'}: raise ValidationError('Choose a status for every student.')
    for student in students:
        row = Attendance.objects.filter(session=session, student=student).first()
        before = snapshot(row)
        previous = row.status if row else None
        row, _ = Attendance.objects.update_or_create(session=session, student=student, defaults={'status': statuses[str(student.pk)], 'marked_by': user})
        audit(user, row, before, 'attendance')
        if row.status == 'absent' and previous != 'absent':
            notify_student(student, 'Absence notification', f'{student.user} was absent from {session.subject.name} on {timezone.localtime(session.starts_at):%d %b %Y %H:%M}.', f'attendance:{row.pk}:absent')


@transaction.atomic
def start_attempt(user, exam):
    student = own_student(user)
    if student.batch_id != exam.batch_id or not exam.published: raise PermissionDenied
    now = timezone.now()
    if not exam.starts_at <= now < exam.ends_at: raise ValidationError('This exam is not open.')
    if exam.kind == 'mcq' and not exam.questions.exists(): raise ValidationError('This exam has no questions yet.')
    attempt, _ = Attempt.objects.get_or_create(exam=exam, student=student, defaults={'deadline': min(exam.ends_at, now + timedelta(minutes=exam.duration_minutes))})
    return attempt


@transaction.atomic
def save_answers(user, attempt_id, answers, submit=False, file=None):
    attempt = Attempt.objects.select_for_update().select_related('exam', 'student').get(pk=attempt_id)
    if attempt.student_id != own_student(user).pk: raise PermissionDenied
    if attempt.submitted_at: return attempt
    now = timezone.now()
    if now < attempt.deadline:
        if attempt.exam.kind == 'mcq':
            allowed = {str(q.pk) for q in attempt.exam.questions.all()}
            attempt.answers = {k: v for k, v in answers.items() if k in allowed and v in 'abcd' and len(v) == 1}
        elif file:
            validate_upload(file)
            attempt.file = file
    if submit or now >= attempt.deadline:
        if attempt.exam.kind == 'written' and not attempt.file and now < attempt.deadline:
            raise ValidationError('Upload your written answer before submitting.')
        finalize_attempt(attempt, now)
    attempt.save()
    return attempt


def finalize_attempt(attempt, now=None):
    if attempt.submitted_at: return
    attempt.submitted_at = now or timezone.now()
    if attempt.exam.kind == 'mcq':
        questions = list(attempt.exam.questions.all())
        total = sum(q.marks for q in questions)
        earned = sum(q.marks for q in questions if attempt.answers.get(str(q.pk)) == q.correct_option)
        attempt.score = (Decimal(earned) / Decimal(total) * attempt.exam.total_marks).quantize(Decimal('.01')) if total else Decimal('0')


@transaction.atomic
def grade(user, model, pk, score, feedback):
    require(user, ACADEMIC)
    row = scoped(model, user).select_for_update().get(pk=pk)
    assessment = row.exam if model == Attempt else row.assignment
    if model == Attempt and not row.submitted_at: raise ValidationError('The exam has not been submitted.')
    if score < 0 or score > assessment.total_marks: raise ValidationError(f'Score must be between 0 and {assessment.total_marks}.')
    before = snapshot(row)
    row.score, row.feedback, row.graded_by = score, feedback, user
    row.save()
    audit(user, row, before, 'grade')
    notify(row.student.user, 'Marking updated', f'Your submission for {assessment.title} has been marked. Exam scores appear after results are published.', f'grade:{model.__name__}:{row.pk}:{row.score}')
    return row
