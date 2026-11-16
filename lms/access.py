from django.core.exceptions import PermissionDenied
from django.db.models import Q
from .models import *

MANAGERS = {'owner', 'admin'}
ACADEMIC = MANAGERS | {'teacher'}
FINANCE = MANAGERS | {'accountant'}


def role(user):
    return 'owner' if user.is_superuser else user.role


def require(user, roles):
    if role(user) not in roles: raise PermissionDenied


def students_for(user):
    qs = Student.objects.select_related('user', 'batch', 'batch__course')
    r = role(user)
    if r in MANAGERS | {'accountant'}: return qs
    if r == 'teacher': return qs.filter(batch__teachers=user).distinct()
    if r == 'student': return qs.filter(user=user)
    if r == 'guardian': return qs.filter(guardians=user).distinct()
    return qs.none()


def batches_for(user):
    qs = Batch.objects.select_related('course')
    if role(user) in MANAGERS | {'accountant'}: return qs
    if role(user) == 'teacher': return qs.filter(teachers=user)
    return qs.filter(active=True, id__in=students_for(user).filter(active=True).values('batch_id'))


def own_student(user):
    require(user, {'student'})
    try: return Student.objects.get(user=user, active=True, batch__active=True)
    except Student.DoesNotExist: raise PermissionDenied('An active admission is required.')


def scoped(model, user):
    qs = model.objects.all()
    r = role(user)
    if r in MANAGERS: return qs
    students = students_for(user)
    batches = batches_for(user)
    if model == Student: return students
    if model == Batch: return batches
    if model in (Course, Subject):
        return qs.filter(**({'id__in': batches.values('course_id')} if model == Course else {'course_id__in': batches.values('course_id')}))
    if model == Material:
        if r == 'accountant': return qs.none()
        return qs.filter(Q(batch__in=batches) | Q(batch=None, course_id__in=batches.values('course_id')))
    if model == Question:
        return qs.filter(exam__batch__in=batches) if r == 'teacher' else qs.none()
    if model in (ClassSession, Exam, Assignment, Notice):
        if r == 'accountant': return qs.none()
        qs = qs.filter(batch__in=batches)
        if model == Exam and r not in ACADEMIC: qs = qs.filter(published=True)
        return qs
    if model in (Invoice, Payment):
        if r == 'teacher': return qs.none()
        return qs.filter(**({'student__in': students} if model == Invoice else {'invoice__student__in': students}))
    if model in (Attempt, Submission, Attendance, Doubt):
        if r == 'accountant': return qs.none()
        if r == 'teacher':
            path = {Attempt: 'exam__batch', Submission: 'assignment__batch', Attendance: 'session__batch', Doubt: 'batch'}[model]
            return qs.filter(**{path + '__in': batches})
        qs = qs.filter(student__in=students)
        if model == Attempt: qs = qs.filter(exam__results_published=True)
        return qs
    return qs.none()
