import uuid
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


def private_path(instance, filename):
    return f'{timezone.now():%Y/%m}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'


def validate_upload(value):
    if value.size > 20 * 1024 * 1024:
        raise ValidationError('Maximum file size is 20 MB. Use a link for recorded classes.')
    if Path(value.name).suffix.lower() not in {'.pdf', '.png', '.jpg', '.jpeg'}:
        raise ValidationError('Upload a PDF, PNG or JPEG file.')
    prefix = value.read(12)
    value.seek(0)
    if not (prefix.startswith(b'%PDF-') or prefix.startswith(b'\x89PNG\r\n\x1a\n') or prefix.startswith(b'\xff\xd8\xff')):
        raise ValidationError('The file contents must be a valid PDF or image.')


class User(AbstractUser):
    ROLES = [(x, x.title()) for x in ('owner', 'admin', 'accountant', 'teacher', 'student', 'guardian')]
    role = models.CharField(max_length=12, choices=ROLES, default='student')
    phone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.get_full_name() or self.username


class Course(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    def __str__(self): return self.name


class Subject(models.Model):
    course = models.ForeignKey(Course, on_delete=models.PROTECT)
    name = models.CharField(max_length=100)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['course', 'name'], name='subject_course_name')]
    def __str__(self): return f'{self.course} · {self.name}'


class Batch(models.Model):
    name = models.CharField(max_length=100)
    course = models.ForeignKey(Course, on_delete=models.PROTECT)
    shift = models.CharField(max_length=40, default='Morning')
    teachers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='teaching_batches', limit_choices_to={'role': 'teacher'})
    active = models.BooleanField(default=True)
    def __str__(self): return f'{self.name} · {self.shift}'


class Student(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='student_profile', limit_choices_to={'role': 'student'})
    admission_no = models.CharField(max_length=30, unique=True)
    admitted_on = models.DateField(default=timezone.localdate)
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT)
    subjects = models.ManyToManyField(Subject, blank=True)
    guardians = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='children', limit_choices_to={'role': 'guardian'})
    guardian_name = models.CharField(max_length=120)
    guardian_phone = models.CharField(max_length=20)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    def __str__(self): return f'{self.admission_no} · {self.user}'


class BatchTransfer(models.Model):
    student = models.ForeignKey(Student, on_delete=models.PROTECT)
    from_batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name='+')
    to_batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name='+')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class ClassSession(models.Model):
    STATUS = [('scheduled', 'Scheduled'), ('cancelled', 'Cancelled'), ('rescheduled', 'Rescheduled')]
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT)
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, limit_choices_to={'role': 'teacher'})
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    room = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=15, choices=STATUS, default='scheduled')
    note = models.CharField(max_length=250, blank=True)
    class Meta: ordering = ['starts_at']
    def clean(self):
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError('Class must end after its start time.')
        if self.batch_id and self.subject_id and self.subject.course_id != self.batch.course_id:
            raise ValidationError('Subject must belong to the batch course.')
        if self.batch_id and self.teacher_id and not self.batch.teachers.filter(pk=self.teacher_id).exists():
            raise ValidationError('Assign this teacher to the batch first.')
        if self.starts_at and self.ends_at and self.status != 'cancelled':
            overlap = ClassSession.objects.exclude(pk=self.pk).exclude(status='cancelled').filter(starts_at__lt=self.ends_at, ends_at__gt=self.starts_at).filter(Q(teacher_id=self.teacher_id) | Q(batch_id=self.batch_id))
            if overlap.exists(): raise ValidationError('Teacher or batch already has a class at this time.')
    def __str__(self): return f'{self.subject.name} · {self.starts_at:%d %b %H:%M}'


class Attendance(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.PROTECT)
    student = models.ForeignKey(Student, on_delete=models.PROTECT)
    status = models.CharField(max_length=8, choices=[('present', 'Present'), ('late', 'Late'), ('absent', 'Absent')])
    marked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['session', 'student'], name='attendance_once')]


class Material(models.Model):
    KINDS = [('pdf', 'Lecture / PDF'), ('notes', 'Notes'), ('questions', 'Question bank'), ('recording', 'Recorded class'), ('live', 'Live class')]
    title = models.CharField(max_length=180)
    course = models.ForeignKey(Course, on_delete=models.PROTECT)
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, null=True, blank=True, help_text='Leave empty for all active students in the course.')
    kind = models.CharField(max_length=12, choices=KINDS)
    file = models.FileField(upload_to=private_path, validators=[validate_upload], blank=True)
    url = models.URLField(blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def clean(self):
        if not self.file and not self.url: raise ValidationError('Upload a file or provide a link.')
        if self.batch_id and self.batch.course_id != self.course_id: raise ValidationError('Batch and course must match.')
        if self.url and not self.url.startswith('https://'): raise ValidationError('Use an HTTPS link.')
    def __str__(self): return self.title


class Exam(models.Model):
    title = models.CharField(max_length=180)
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT)
    kind = models.CharField(max_length=10, choices=[('mcq', 'MCQ'), ('written', 'Written')])
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=30, validators=[MinValueValidator(1), MaxValueValidator(360)])
    total_marks = models.PositiveIntegerField(default=100, validators=[MinValueValidator(1)])
    instructions = models.TextField(blank=True)
    published = models.BooleanField(default=False, help_text='Make exam available to students.')
    results_published = models.BooleanField(default=False)
    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at: raise ValidationError('Exam must end after it starts.')
        if self.batch_id and self.subject_id and self.subject.course_id != self.batch.course_id: raise ValidationError('Subject and batch course must match.')
    def __str__(self): return self.title


class Question(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='questions', limit_choices_to={'kind': 'mcq'})
    text = models.TextField()
    option_a = models.CharField(max_length=500)
    option_b = models.CharField(max_length=500)
    option_c = models.CharField(max_length=500)
    option_d = models.CharField(max_length=500)
    correct_option = models.CharField(max_length=1, choices=[(x, x.upper()) for x in 'abcd'])
    marks = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    class Meta: ordering = ['id']
    def __str__(self): return self.text[:80]


class Attempt(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.PROTECT)
    student = models.ForeignKey(Student, on_delete=models.PROTECT)
    started_at = models.DateTimeField(default=timezone.now)
    deadline = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    answers = models.JSONField(default=dict)
    file = models.FileField(upload_to=private_path, validators=[validate_upload], blank=True)
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    graded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['exam', 'student'], name='one_exam_attempt')]


class Assignment(models.Model):
    title = models.CharField(max_length=180)
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT)
    instructions = models.TextField()
    deadline = models.DateTimeField()
    total_marks = models.PositiveIntegerField(default=100, validators=[MinValueValidator(1)])
    def clean(self):
        if self.batch_id and self.subject_id and self.subject.course_id != self.batch.course_id: raise ValidationError('Subject and batch course must match.')
    def __str__(self): return self.title


class Submission(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT)
    student = models.ForeignKey(Student, on_delete=models.PROTECT)
    file = models.FileField(upload_to=private_path, validators=[validate_upload])
    note = models.TextField(blank=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    graded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['assignment', 'student'], name='one_submission')]


class Invoice(models.Model):
    student = models.ForeignKey(Student, on_delete=models.PROTECT)
    title = models.CharField(max_length=150)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    due_date = models.DateField()
    @property
    def paid(self): return sum((p.amount for p in self.payments.all()), Decimal('0'))
    @property
    def balance(self): return self.amount - self.paid
    def __str__(self): return f'{self.student} · {self.title}'


class Payment(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    method = models.CharField(max_length=12, choices=[('cash', 'Cash'), ('bkash', 'bKash'), ('nagad', 'Nagad'), ('bank', 'Bank')])
    reference = models.CharField(max_length=100, unique=True, help_text='Unique receipt or transaction number.')
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return f'{self.reference} · ৳{self.amount}'


class Notice(models.Model):
    title = models.CharField(max_length=160)
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.title


class Doubt(models.Model):
    student = models.ForeignKey(Student, on_delete=models.PROTECT)
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT)
    question = models.TextField()
    answer = models.TextField(blank=True)
    answered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.question[:90]


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=180)
    body = models.TextField()
    read = models.BooleanField(default=False)
    dedupe_key = models.CharField(max_length=180, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-created_at']


class Delivery(models.Model):
    channel = models.CharField(max_length=8, choices=[('sms', 'SMS'), ('push', 'Push')])
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    title = models.CharField(max_length=180)
    body = models.TextField()
    dedupe_key = models.CharField(max_length=200, unique=True)
    state = models.CharField(max_length=10, default='pending', choices=[('pending', 'Pending'), ('sending', 'Sending'), ('sent', 'Sent'), ('failed', 'Failed')])
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=250, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class PushSubscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    endpoint = models.URLField(max_length=1000, unique=True)
    keys = models.JSONField()


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    entity = models.CharField(max_length=60)
    object_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=30)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-created_at']


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    window_start = models.DateTimeField(default=timezone.now)
