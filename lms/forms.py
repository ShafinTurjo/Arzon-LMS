from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import *
from .access import *

# Explicit field allowlists keep ownership, scores and audit fields out of ordinary edit forms.
REGISTRY = {
    'students': (Student, ['user', 'admission_no', 'admitted_on', 'batch', 'subjects', 'guardians', 'guardian_name', 'guardian_phone', 'address', 'active'], MANAGERS),
    'courses': (Course, ['name', 'description'], MANAGERS),
    'subjects': (Subject, ['course', 'name'], MANAGERS),
    'batches': (Batch, ['name', 'course', 'shift', 'teachers', 'active'], MANAGERS),
    'routine': (ClassSession, ['batch', 'subject', 'teacher', 'starts_at', 'ends_at', 'room', 'status', 'note'], ACADEMIC),
    'materials': (Material, ['title', 'course', 'batch', 'kind', 'file', 'url', 'description'], ACADEMIC),
    'exams': (Exam, ['title', 'batch', 'subject', 'kind', 'starts_at', 'ends_at', 'duration_minutes', 'total_marks', 'instructions', 'published', 'results_published'], ACADEMIC),
    'questions': (Question, ['exam', 'text', 'option_a', 'option_b', 'option_c', 'option_d', 'correct_option', 'marks'], ACADEMIC),
    'assignments': (Assignment, ['title', 'batch', 'subject', 'instructions', 'deadline', 'total_marks'], ACADEMIC),
    'invoices': (Invoice, ['student', 'title', 'amount', 'due_date'], FINANCE),
    'payments': (Payment, ['invoice', 'amount', 'method', 'reference'], FINANCE),
    'notices': (Notice, ['title', 'batch', 'body'], ACADEMIC),
}


def record_form(kind, user, data=None, files=None, instance=None):
    model, fields, roles = REGISTRY[kind]
    Form = forms.modelform_factory(model, fields=fields)
    form = Form(data, files, instance=instance)
    for name, field in form.fields.items():
        if isinstance(field, forms.DateTimeField): field.widget = forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'})
        elif isinstance(field, forms.DateField): field.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})
        if isinstance(field, forms.ModelChoiceField) or isinstance(field, forms.ModelMultipleChoiceField):
            related = field.queryset.model
            if related in (Batch, Course, Subject, Exam, Student, Invoice):
                field.queryset = scoped(related, user)
            elif related == User:
                field.queryset = field.queryset.filter(is_active=True)
    if role(user) == 'teacher':
        if 'teacher' in form.fields: form.fields['teacher'].queryset = User.objects.filter(pk=user.pk)
        if model == Material: form.fields['batch'].required = True
    if instance and instance.pk:
        # Keep historical associations immutable. Transfers have their own logged path via Student.batch.
        frozen = {Batch: ['course'], Subject: ['course'], ClassSession: ['batch'], Exam: ['batch'], Assignment: ['batch'], Invoice: ['student'], Payment: ['invoice'], Student: ['user'], Question: ['exam']}.get(model, [])
        for name in frozen: form.fields[name].disabled = True
    return form


def validate_record(form):
    obj = form.instance
    cleaned = form.cleaned_data
    if isinstance(obj, Student):
        if any(s.course_id != obj.batch.course_id for s in cleaned['subjects']):
            raise ValidationError('Selected subjects must belong to the student’s batch course.')
    if isinstance(obj, Exam) and obj.pk and obj.attempt_set.exists():
        protected = {'kind', 'subject', 'starts_at', 'ends_at', 'duration_minutes', 'total_marks'}
        if protected.intersection(form.changed_data): raise ValidationError('Exam timing, subject, type and marks are locked after the first attempt.')
    if isinstance(obj, Assignment) and obj.pk and obj.submission_set.exists():
        if {'subject', 'total_marks'}.intersection(form.changed_data):
            raise ValidationError('Assignment subject and total marks are locked after the first submission.')
    if isinstance(obj, Question) and obj.exam.attempt_set.exists():
        raise ValidationError('Questions are locked after the first attempt.')
    if isinstance(obj, Exam) and obj.results_published and not obj.published:
        raise ValidationError('Publish the exam before publishing results.')
    if isinstance(obj, Invoice):
        paid = obj.paid if obj.pk else 0
        if obj.amount < paid: raise ValidationError('Invoice amount cannot be below payments already received.')
    if isinstance(obj, Payment):
        others = obj.invoice.payments.exclude(pk=obj.pk).aggregate(total=models.Sum('amount'))['total'] or 0
        if obj.amount + others > obj.invoice.amount: raise ValidationError('Payment exceeds the outstanding balance.')


class AccountForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, required=False, help_text='Required for a new account. Leave empty to keep an existing password.')
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'role', 'is_active']
    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        if role(actor) != 'owner': self.fields['role'].choices = [(k, v) for k, v in User.ROLES if k not in MANAGERS]
        if self.instance.pk:
            # Reassigning roles could invalidate guardian/student/teacher relationships.
            self.fields['role'].disabled = True
    def clean_password(self):
        password = self.cleaned_data.get('password')
        if not self.instance.pk and not password: raise ValidationError('Set an initial password.')
        if password:
            candidate = User(username=self.cleaned_data.get('username', ''), first_name=self.cleaned_data.get('first_name', ''), last_name=self.cleaned_data.get('last_name', ''), email=self.cleaned_data.get('email', ''))
            validate_password(password, candidate)
        return password
    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data['password']: user.set_password(self.cleaned_data['password'])
        if commit: user.save()
        return user


class UploadForm(forms.Form):
    file = forms.FileField(validators=[validate_upload])
    note = forms.CharField(widget=forms.Textarea, required=False)


class GradeForm(forms.Form):
    score = forms.DecimalField(min_value=0, decimal_places=2, max_digits=7)
    feedback = forms.CharField(widget=forms.Textarea, required=False)


class DoubtForm(forms.Form):
    subject = forms.ModelChoiceField(queryset=Subject.objects.none())
    question = forms.CharField(widget=forms.Textarea, max_length=5000)
