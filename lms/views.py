import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .access import *
from .forms import *
from .services import *


def fail_message(exc): return ' '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


@login_required
def dashboard(request):
    user = request.user
    r = role(user)
    students = students_for(user)
    invoices = scoped(Invoice, user).prefetch_related('payments')
    fees = sum((i.balance for i in invoices), Decimal('0'))
    paid = scoped(Payment, user).aggregate(total=Sum('amount'))['total'] or 0
    attendance = scoped(Attendance, user)
    count = attendance.count()
    rate = round(attendance.exclude(status='absent').count() / count * 100) if count else None
    sessions = scoped(ClassSession, user).filter(ends_at__gte=timezone.now()).select_related('batch', 'subject', 'teacher')[:6]
    return render(request, 'lms/dashboard.html', {'title': 'Overview', 'student_count': students.filter(active=True).count(), 'batch_count': batches_for(user).count(), 'dues': fees, 'collected': paid, 'attendance_rate': rate, 'sessions': sessions, 'children': students if r in {'guardian', 'student'} else [], 'recent_notices': scoped(Notice, user).order_by('-created_at')[:4], 'recent_notifications': Notification.objects.filter(user=user)[:4]})


@login_required
def record_list(request, kind):
    if kind not in REGISTRY: raise Http404
    model, fields, editors = REGISTRY[kind]
    if kind == 'questions': require(request.user, ACADEMIC)
    if kind in {'invoices', 'payments'}: require(request.user, FINANCE | {'student', 'guardian'})
    qs = scoped(model, request.user).order_by('-pk')
    relations = {Student: ['user', 'batch'], Subject: ['course'], Batch: ['course'], ClassSession: ['subject', 'batch', 'teacher'], Material: ['course', 'batch'], Exam: ['batch'], Question: ['exam'], Assignment: ['batch'], Invoice: ['student__user'], Payment: ['invoice__student__user'], Notice: ['batch']}.get(model, [])
    if relations: qs = qs.select_related(*relations)
    if model == Invoice: qs = qs.prefetch_related('payments')
    q = request.GET.get('q', '').strip()[:100]
    search_fields = {Student: ['admission_no', 'user__first_name', 'user__last_name', 'guardian_phone'], ClassSession: ['batch__name', 'subject__name'], Payment: ['reference'], Subject: ['name'], Batch: ['name'], Course: ['name'], Question: ['text']}.get(model, ['title'])
    if q:
        query = Q()
        for f in search_fields: query |= Q(**{f + '__icontains': q})
        qs = qs.filter(query)
    if request.GET.get('batch', '').isdigit():
        if 'batch' in fields: qs = qs.filter(batch_id=request.GET['batch'])
    if model == Question and request.GET.get('exam', '').isdigit(): qs = qs.filter(exam_id=request.GET['exam'])
    page = Paginator(qs, 20).get_page(request.GET.get('page'))
    columns = {
        Student: ['Student', 'Batch', 'Guardian', 'Status'], Course: ['Course', 'Description'], Subject: ['Subject', 'Course'], Batch: ['Batch', 'Course', 'Shift', 'Status'], ClassSession: ['Class', 'Batch', 'Teacher', 'When', 'Status'], Material: ['Title', 'Access', 'Type'], Exam: ['Exam', 'Batch', 'Type', 'Opens', 'Status'], Question: ['Question', 'Exam', 'Marks'], Assignment: ['Assignment', 'Batch', 'Deadline'], Invoice: ['Student / fee', 'Total', 'Paid', 'Due', 'Due date'], Payment: ['Receipt', 'Student / fee', 'Amount', 'Method'], Notice: ['Notice', 'Batch', 'Published']
    }[model]
    rows = []
    for obj in page:
        actions = []
        if model == Student:
            cells = [str(obj), str(obj.batch), f'{obj.guardian_name} · {obj.guardian_phone}', 'Active' if obj.active else 'Inactive']
            actions.append(('Profile', reverse('student_detail', args=[obj.pk])))
        elif model == Course: cells = [obj.name, obj.description[:120]]
        elif model == Subject: cells = [obj.name, str(obj.course)]
        elif model == Batch: cells = [obj.name, str(obj.course), obj.shift, 'Active' if obj.active else 'Inactive']
        elif model == ClassSession:
            cells = [obj.subject.name, str(obj.batch), str(obj.teacher), timezone.localtime(obj.starts_at).strftime('%d %b, %I:%M %p'), obj.get_status_display()]
            if role(request.user) in ACADEMIC: actions.append(('Attendance', reverse('attendance_mark', args=[obj.pk])))
        elif model == Material:
            cells = [obj.title, str(obj.batch or obj.course), obj.get_kind_display()]
            if obj.file: actions.append(('Download', reverse('download', args=['material', obj.pk])))
            if obj.url: actions.append(('Open link', obj.url))
        elif model == Exam:
            cells = [obj.title, str(obj.batch), obj.get_kind_display(), timezone.localtime(obj.starts_at).strftime('%d %b, %I:%M %p'), 'Published' if obj.published else 'Draft']
            actions.append(('Open exam', reverse('exam_detail', args=[obj.pk])))
        elif model == Question: cells = [obj.text[:90], obj.exam.title, obj.marks]
        elif model == Assignment:
            cells = [obj.title, str(obj.batch), timezone.localtime(obj.deadline).strftime('%d %b, %I:%M %p')]
            actions.append(('Open', reverse('assignment_detail', args=[obj.pk])))
        elif model == Invoice: cells = [str(obj), f'৳{obj.amount}', f'৳{obj.paid}', f'৳{obj.balance}', obj.due_date]
        elif model == Payment: cells = [obj.reference, str(obj.invoice), f'৳{obj.amount}', obj.get_method_display()]
        else:
            cells = [obj.title, str(obj.batch), timezone.localtime(obj.created_at).strftime('%d %b %Y')]
            actions.append(('Read', reverse('notice_detail', args=[obj.pk])))
        writable = role(request.user) in editors and not (model == Material and role(request.user) == 'teacher' and obj.batch_id is None)
        if writable: actions.append(('Edit', reverse('record_edit', args=[kind, obj.pk])))
        rows.append({'cells': cells, 'actions': actions})
    return render(request, 'lms/list.html', {'title': kind.replace('_', ' ').title(), 'columns': columns, 'rows': rows, 'page': page, 'q': q, 'create_url': reverse('record_create', args=[kind]) if role(request.user) in editors else '', 'kind': kind})


@login_required
@transaction.atomic
def record_edit(request, kind, pk=None):
    if kind not in REGISTRY: raise Http404
    model, fields, editors = REGISTRY[kind]
    require(request.user, editors)
    editable = scoped(model, request.user)
    if model == Material and role(request.user) == 'teacher': editable = editable.filter(batch__in=batches_for(request.user))
    obj = get_object_or_404(editable.select_for_update(), pk=pk) if pk else None
    before = snapshot(obj)
    old_batch = obj.batch if isinstance(obj, Student) else None
    if isinstance(obj, Payment): Invoice.objects.select_for_update().get(pk=obj.invoice_id)
    form = record_form(kind, request.user, request.POST or None, request.FILES or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        try:
            item = form.save(commit=False)
            if model == Payment:
                Invoice.objects.select_for_update().get(pk=item.invoice_id)
                if not item.pk: item.received_by = request.user
            validate_record(form)
            item.save()
            form.save_m2m()
            audit(request.user, item, before, 'update' if pk else 'create')
            if model == Student and old_batch and old_batch.pk != item.batch_id:
                BatchTransfer.objects.create(student=item, from_batch=old_batch, to_batch=item.batch, actor=request.user)
            if model == ClassSession and pk and set(form.changed_data) & {'starts_at', 'ends_at', 'status'}:
                notify_batch(item.batch, 'Class routine updated', f'{item.subject.name}: {item.get_status_display()} · {timezone.localtime(item.starts_at):%d %b %H:%M}. {item.note}', f'class:{item.pk}:{timezone.now().isoformat()}')
            if model == Notice:
                notify_batch(item.batch, item.title, item.body, f'notice:{item.pk}:{timezone.now().isoformat()}')
            messages.success(request, 'Saved successfully.')
            return redirect('records', kind=kind)
        except ValidationError as exc: form.add_error(None, exc)
    return render(request, 'lms/form.html', {'title': ('Edit ' if pk else 'Add ') + kind.rstrip('s'), 'form': form, 'back_url': reverse('records', args=[kind])})


@login_required
def student_detail(request, pk):
    student = get_object_or_404(students_for(request.user), pk=pk)
    attendance = scoped(Attendance, request.user).filter(student=student).select_related('session__subject').order_by('-session__starts_at')[:30]
    attempts = scoped(Attempt, request.user).filter(student=student, score__isnull=False).select_related('exam__subject')
    invoices = scoped(Invoice, request.user).filter(student=student).prefetch_related('payments')
    return render(request, 'lms/student.html', {'title': str(student.user), 'student': student, 'attendance': attendance, 'attempts': attempts, 'invoices': invoices})


@login_required
def accounts(request):
    require(request.user, MANAGERS)
    qs = User.objects.order_by('username')
    if role(request.user) != 'owner': qs = qs.exclude(role__in=MANAGERS).exclude(is_superuser=True)
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    rows = [{'cells': [u.username, str(u), u.get_role_display(), 'Active' if u.is_active else 'Inactive'], 'actions': [('Edit', reverse('account_edit', args=[u.pk]))]} for u in page]
    return render(request, 'lms/list.html', {'title': 'Accounts', 'columns': ['Username', 'Name', 'Role', 'Status'], 'rows': rows, 'page': page, 'create_url': reverse('account_create')})


@login_required
@transaction.atomic
def account_edit(request, pk=None):
    require(request.user, MANAGERS)
    obj = get_object_or_404(User, pk=pk) if pk else None
    if obj and role(request.user) != 'owner' and (obj.role in MANAGERS or obj.is_superuser): raise PermissionDenied
    before = snapshot(obj)
    form = AccountForm(request.POST or None, instance=obj, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        if obj and obj.pk == request.user.pk and not form.cleaned_data['is_active']:
            form.add_error('is_active', 'You cannot deactivate your own account.')
        else:
            user = form.save()
            audit(request.user, user, before, 'account_update' if pk else 'account_create')
            messages.success(request, 'Account saved. Share the initial password securely.')
            return redirect('accounts')
    return render(request, 'lms/form.html', {'title': 'Edit account' if pk else 'Create account', 'form': form, 'back_url': reverse('accounts')})


@login_required
def attendance_list(request):
    qs = scoped(Attendance, request.user).select_related('student__user', 'session__subject', 'session__batch').order_by('-session__starts_at')
    page = Paginator(qs, 30).get_page(request.GET.get('page'))
    rows = [{'cells': [str(a.student), str(a.session), a.get_status_display(), timezone.localtime(a.updated_at).strftime('%d %b %H:%M')], 'actions': []} for a in page]
    return render(request, 'lms/list.html', {'title': 'Attendance', 'columns': ['Student', 'Class', 'Status', 'Updated'], 'rows': rows, 'page': page, 'description': 'Open a class in Class routine to mark attendance.' if role(request.user) in ACADEMIC else 'Your attendance history, including late arrivals and absences.'})


@login_required
def attendance_mark(request, pk):
    require(request.user, ACADEMIC)
    session = get_object_or_404(scoped(ClassSession, request.user), pk=pk)
    students = Student.objects.filter(batch=session.batch, active=True).select_related('user')
    current = {a.student_id: a.status for a in Attendance.objects.filter(session=session)}
    if request.method == 'POST':
        try:
            mark_attendance(request.user, session, {str(s.pk): request.POST.get(f'student_{s.pk}') for s in students})
            messages.success(request, 'Attendance saved. Absence notifications have been queued.')
            return redirect('attendance')
        except ValidationError as exc: messages.error(request, fail_message(exc))
    return render(request, 'lms/attendance.html', {'title': 'Mark attendance', 'session': session, 'rows': [{'student': s, 'status': current.get(s.pk, '')} for s in students]})


@login_required
def exam_detail(request, pk):
    exam = get_object_or_404(scoped(Exam, request.user), pk=pk)
    attempt = None
    if role(request.user) == 'student': attempt = Attempt.objects.filter(exam=exam, student__user=request.user).first()
    submissions = scoped(Attempt, request.user).filter(exam=exam).select_related('student__user') if role(request.user) in ACADEMIC else []
    return render(request, 'lms/exam.html', {'title': exam.title, 'exam': exam, 'attempt': attempt, 'submissions': submissions, 'question_count': exam.questions.count()})


@login_required
@require_POST
def exam_start(request, pk):
    exam = get_object_or_404(scoped(Exam, request.user), pk=pk)
    try:
        attempt = start_attempt(request.user, exam)
        return redirect('exam_take', pk=attempt.pk)
    except ValidationError as exc:
        messages.error(request, fail_message(exc))
        return redirect('exam_detail', pk=pk)


@login_required
def exam_take(request, pk):
    student = own_student(request.user)
    attempt = get_object_or_404(Attempt.objects.select_related('exam'), pk=pk, student=student)
    if attempt.exam.batch_id != student.batch_id: raise PermissionDenied
    if request.method == 'POST' or timezone.now() >= attempt.deadline:
        try:
            answers = {k[2:]: v for k, v in request.POST.items() if k.startswith('q_')}
            attempt = save_answers(request.user, pk, answers, request.POST.get('action') == 'submit', request.FILES.get('file'))
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'saved': True, 'submitted': bool(attempt.submitted_at)})
            messages.success(request, 'Exam submitted.' if attempt.submitted_at else 'Answers saved.')
        except ValidationError as exc: messages.error(request, fail_message(exc))
    if attempt.submitted_at: return redirect('exam_detail', pk=attempt.exam_id)
    questions = [{'id': q.pk, 'text': q.text, 'marks': q.marks, 'options': [(key, getattr(q, f'option_{key}')) for key in 'abcd'], 'selected': attempt.answers.get(str(q.pk), '')} for q in attempt.exam.questions.all()]
    return render(request, 'lms/take_exam.html', {'title': attempt.exam.title, 'attempt': attempt, 'questions': questions})


@login_required
@transaction.atomic
def assignment_detail(request, pk):
    assignment = get_object_or_404(scoped(Assignment, request.user), pk=pk)
    form, submission = None, None
    if role(request.user) == 'student':
        student = own_student(request.user)
        submission = Submission.objects.filter(assignment=assignment, student=student).first()
        form = UploadForm(request.POST or None, request.FILES or None)
        if request.method == 'POST' and form.is_valid():
            if timezone.now() > assignment.deadline: form.add_error(None, 'The submission deadline has passed.')
            elif submission: form.add_error(None, 'You have already submitted this assignment.')
            else:
                Submission.objects.create(assignment=assignment, student=student, file=form.cleaned_data['file'], note=form.cleaned_data['note'])
                messages.success(request, 'Assignment submitted.')
                return redirect('assignment_detail', pk=pk)
    elif request.method == 'POST': raise PermissionDenied
    submissions = scoped(Submission, request.user).filter(assignment=assignment).select_related('student__user') if role(request.user) in ACADEMIC else []
    return render(request, 'lms/assignment.html', {'title': assignment.title, 'assignment': assignment, 'form': form, 'submission': submission, 'submissions': submissions})


@login_required
def grade_view(request, kind, pk):
    require(request.user, ACADEMIC)
    model = {'exam': Attempt, 'assignment': Submission}.get(kind)
    if not model: raise Http404
    row = get_object_or_404(scoped(model, request.user), pk=pk)
    form = GradeForm(request.POST or None, initial={'score': row.score, 'feedback': row.feedback})
    if request.method == 'POST' and form.is_valid():
        try:
            grade(request.user, model, pk, form.cleaned_data['score'], form.cleaned_data['feedback'])
            messages.success(request, 'Mark saved and change history recorded.')
            return redirect('exam_detail' if kind == 'exam' else 'assignment_detail', pk=row.exam_id if kind == 'exam' else row.assignment_id)
        except ValidationError as exc: form.add_error(None, exc)
    return render(request, 'lms/form.html', {'title': f'Mark submission · {row.student}', 'form': form, 'download_url': reverse('download', args=['attempt' if kind == 'exam' else 'submission', pk]) if row.file else None})


@login_required
def results(request):
    attempts = scoped(Attempt, request.user).filter(score__isnull=False).select_related('exam__subject', 'student__user')
    submissions = scoped(Submission, request.user).filter(score__isnull=False).select_related('assignment__subject', 'student__user')
    students = students_for(request.user)
    student_id = request.GET.get('student', '')
    if student_id.isdigit():
        student = get_object_or_404(students, pk=student_id)
        attempts, submissions = attempts.filter(student=student), submissions.filter(student=student)
    progress = {}
    rows = []
    for item in list(attempts) + list(submissions):
        assessment = item.exam if isinstance(item, Attempt) else item.assignment
        key = (item.student_id, assessment.subject_id)
        value = progress.setdefault(key, {'student': str(item.student.user), 'subject': assessment.subject.name, 'score': Decimal(0), 'total': 0})
        value['score'] += item.score
        value['total'] += assessment.total_marks
        rows.append({'student': item.student, 'assessment': assessment.title, 'score': item.score, 'total': assessment.total_marks, 'feedback': item.feedback})
    for value in progress.values(): value['percentage'] = round(value['score'] / value['total'] * 100, 1) if value['total'] else 0
    exams = scoped(Exam, request.user).filter(results_published=True)
    return render(request, 'lms/results.html', {'title': 'Results & progress', 'rows': rows, 'progress': list(progress.values()), 'students': students, 'selected_student': student_id, 'exams': exams})


@login_required
def merit(request, pk):
    exam = get_object_or_404(scoped(Exam, request.user), pk=pk, results_published=True)
    attempts = list(Attempt.objects.filter(exam=exam, score__isnull=False).select_related('student__user').order_by('-score', 'student__admission_no'))
    rows, last_score, rank = [], None, 0
    for index, a in enumerate(attempts, 1):
        if a.score != last_score: rank = index
        last_score = a.score
        rows.append({'cells': [rank, a.student.admission_no, str(a.student.user), f'{a.score} / {exam.total_marks}'], 'actions': []})
    return render(request, 'lms/list.html', {'title': f'Merit list · {exam.title}', 'columns': ['Rank', 'Admission', 'Student', 'Score'], 'rows': rows})


@login_required
def download(request, kind, pk):
    model = {'material': Material, 'attempt': Attempt, 'submission': Submission}.get(kind)
    if not model: raise Http404
    qs = scoped(model, request.user)
    if model in (Attempt, Submission) and role(request.user) in {'student', 'guardian'}:
        qs = model.objects.filter(student__in=students_for(request.user))
    obj = get_object_or_404(qs, pk=pk)
    if not obj.file: raise Http404
    try: response = FileResponse(obj.file.open('rb'), as_attachment=True, filename=f'{kind}-{obj.pk}{Path(obj.file.name).suffix}')
    except FileNotFoundError: raise Http404
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
def notice_detail(request, pk):
    notice = get_object_or_404(scoped(Notice, request.user), pk=pk)
    return render(request, 'lms/notice.html', {'title': notice.title, 'notice': notice})


@login_required
def doubts(request):
    form = None
    if role(request.user) == 'student':
        student = own_student(request.user)
        form = DoubtForm(request.POST or None)
        form.fields['subject'].queryset = Subject.objects.filter(course=student.batch.course)
        if request.method == 'POST' and form.is_valid():
            Doubt.objects.create(student=student, batch=student.batch, **form.cleaned_data)
            messages.success(request, 'Question sent to your batch teachers.')
            return redirect('doubts')
    elif request.method == 'POST': raise PermissionDenied
    rows = scoped(Doubt, request.user).select_related('student__user', 'subject', 'answered_by').order_by('-created_at')
    page = Paginator(rows, 20).get_page(request.GET.get('page'))
    return render(request, 'lms/doubts.html', {'title': 'Questions & doubts', 'form': form, 'page': page})


@login_required
@require_POST
@transaction.atomic
def answer_doubt(request, pk):
    require(request.user, ACADEMIC)
    obj = get_object_or_404(scoped(Doubt, request.user), pk=pk)
    answer = request.POST.get('answer', '').strip()
    if not answer or len(answer) > 10000:
        messages.error(request, 'Provide an answer of 1–10,000 characters.')
    else:
        before = snapshot(obj)
        obj.answer, obj.answered_by = answer, request.user
        obj.save()
        audit(request.user, obj, before, 'answer')
        notify(obj.student.user, 'Your question has an answer', answer[:300], f'doubt:{obj.pk}:{timezone.now().isoformat()}')
        messages.success(request, 'Answer sent.')
    return redirect('doubts')


@login_required
def notifications(request):
    if request.method == 'POST':
        Notification.objects.filter(user=request.user, read=False).update(read=True)
        return redirect('notifications')
    page = Paginator(Notification.objects.filter(user=request.user), 25).get_page(request.GET.get('page'))
    return render(request, 'lms/notifications.html', {'title': 'Notifications', 'page': page, 'vapid_key': settings.VAPID_PUBLIC_KEY})


@login_required
@require_POST
def push_subscribe(request):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict): raise ValueError
        endpoint, keys = data['endpoint'], data['keys']
        if not isinstance(endpoint, str) or not isinstance(keys, dict) or len(endpoint) > 1000: raise ValueError
        host = urlparse(endpoint).hostname or ''
        # Restrict outbound destinations to browser push services to prevent SSRF.
        allowed = ('fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com', 'notify.windows.com')
        if not endpoint.startswith('https://') or not any(host == h or host.endswith('.' + h) for h in allowed): raise ValueError
        if not all(isinstance(keys.get(k), str) and 10 <= len(keys[k]) <= 200 for k in ('p256dh', 'auth')): raise ValueError
        PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={'user': request.user, 'keys': keys})
    except (KeyError, ValueError, TypeError): return JsonResponse({'error': 'Invalid push subscription.'}, status=400)
    return JsonResponse({'saved': True})


def service_worker(request):
    return FileResponse((Path(__file__).parent / 'static/lms/sw.js').open('rb'), content_type='application/javascript')


@login_required
def history(request):
    require(request.user, MANAGERS)
    page = Paginator(AuditEvent.objects.select_related('actor'), 30).get_page(request.GET.get('page'))
    return render(request, 'lms/history.html', {'title': 'Change history', 'page': page})


@login_required
def deliveries(request):
    require(request.user, MANAGERS)
    page = Paginator(Delivery.objects.order_by('-pk'), 30).get_page(request.GET.get('page'))
    rows = [{'cells': [d.title, d.channel.upper(), str(d.user or d.phone), d.state, d.attempts, d.last_error], 'actions': []} for d in page]
    return render(request, 'lms/list.html', {'title': 'Delivery status', 'columns': ['Message', 'Channel', 'Recipient', 'State', 'Attempts', 'Last error'], 'rows': rows, 'page': page, 'description': 'SMS and browser push require configured providers and the scheduled delivery worker. In-app notifications are available immediately.'})
