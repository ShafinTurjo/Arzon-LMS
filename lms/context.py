from .access import role, MANAGERS, ACADEMIC, FINANCE
from .models import Notification


def navigation(request):
    if not request.user.is_authenticated: return {}
    r = role(request.user)
    links = [('Overview', '/', '◈')]
    if r in MANAGERS | {'teacher', 'accountant'}: links.append(('Students', '/records/students/', '◎'))
    if r in MANAGERS:
        links += [('Courses', '/records/courses/', '▦'), ('Subjects', '/records/subjects/', '▤'), ('Batches', '/records/batches/', '◫'), ('Accounts', '/accounts/', '♙')]
    if r != 'accountant':
        links += [('Class routine', '/records/routine/', '▦'), ('Attendance', '/attendance/', '✓'), ('Learning library', '/records/materials/', '▤'), ('Exams', '/records/exams/', '◇'), ('Assignments', '/records/assignments/', '▧'), ('Results & progress', '/results/', '↗')]
    if r in FINANCE | {'student', 'guardian'}: links.append(('Fees & dues', '/records/invoices/', '৳'))
    if r in FINANCE: links.append(('Payments', '/records/payments/', '↔'))
    if r != 'accountant': links += [('Notice board', '/records/notices/', '◉'), ('Questions & doubts', '/doubts/', '?')]
    links.append(('Notifications', '/notifications/', '◌'))
    if r in MANAGERS: links += [('Change history', '/history/', '↺'), ('Delivery status', '/deliveries/', '↗')]
    return {'nav_links': links, 'current_role': r, 'unread_count': Notification.objects.filter(user=request.user, read=False).count(), 'is_academic': r in ACADEMIC, 'is_manager': r in MANAGERS}
