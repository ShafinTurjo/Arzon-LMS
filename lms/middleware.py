import hashlib
from datetime import timedelta
from django.db.models import F
from django.http import HttpResponse
from django.utils import timezone
from .models import LoginAttempt


class LoginThrottleMiddleware:
    """DB-backed throttle shared by workers; trusts REMOTE_ADDR, never arbitrary forwarding headers."""
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        rows = []
        if request.path == '/login/' and request.method == 'POST':
            for value, limit in [(f'ip:{request.META.get("REMOTE_ADDR", "")}', 30), (f'user:{request.POST.get("username", "").casefold()}', 10)]:
                key = hashlib.sha256(value.encode()).hexdigest()
                row, _ = LoginAttempt.objects.get_or_create(key=key)
                if row.window_start < timezone.now() - timedelta(minutes=15):
                    row.failures, row.window_start = 0, timezone.now()
                    row.save()
                if row.failures >= limit:
                    response = HttpResponse('Too many sign-in attempts. Try again in 15 minutes.', status=429)
                    response['Retry-After'] = '900'
                    return response
                rows.append(row)
        response = self.get_response(request)
        if rows and response.status_code == 200:
            LoginAttempt.objects.filter(pk__in=[r.pk for r in rows]).update(failures=F('failures') + 1)
        elif rows and response.status_code == 302:
            LoginAttempt.objects.filter(pk=rows[-1].pk).delete()
        if request.user.is_authenticated:
            response['Cache-Control'] = 'private, no-store'
        response['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response['Referrer-Policy'] = 'same-origin'
        return response
