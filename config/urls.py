from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from lms import views as v

urlpatterns = [
    path('system-admin/', admin.site.urls),
    path('login/', auth.LoginView.as_view(template_name='lms/login.html'), name='login'),
    path('logout/', auth.LogoutView.as_view(), name='logout'),
    path('password/', auth.PasswordChangeView.as_view(template_name='lms/form.html', success_url='/'), name='password_change'),
    path('', v.dashboard, name='dashboard'),
    path('records/<str:kind>/', v.record_list, name='records'),
    path('records/<str:kind>/new/', v.record_edit, name='record_create'),
    path('records/<str:kind>/<int:pk>/edit/', v.record_edit, name='record_edit'),
    path('students/<int:pk>/', v.student_detail, name='student_detail'),
    path('accounts/', v.accounts, name='accounts'),
    path('accounts/new/', v.account_edit, name='account_create'),
    path('accounts/<int:pk>/edit/', v.account_edit, name='account_edit'),
    path('attendance/', v.attendance_list, name='attendance'),
    path('attendance/<int:pk>/', v.attendance_mark, name='attendance_mark'),
    path('exams/<int:pk>/', v.exam_detail, name='exam_detail'),
    path('exams/<int:pk>/start/', v.exam_start, name='exam_start'),
    path('attempts/<int:pk>/', v.exam_take, name='exam_take'),
    path('assignments/<int:pk>/', v.assignment_detail, name='assignment_detail'),
    path('grade/<str:kind>/<int:pk>/', v.grade_view, name='grade'),
    path('results/', v.results, name='results'),
    path('results/<int:pk>/merit/', v.merit, name='merit'),
    path('download/<str:kind>/<int:pk>/', v.download, name='download'),
    path('notices/<int:pk>/', v.notice_detail, name='notice_detail'),
    path('doubts/', v.doubts, name='doubts'),
    path('doubts/<int:pk>/answer/', v.answer_doubt, name='answer_doubt'),
    path('notifications/', v.notifications, name='notifications'),
    path('push/subscribe/', v.push_subscribe, name='push_subscribe'),
    path('sw.js', v.service_worker, name='service_worker'),
    path('history/', v.history, name='history'),
    path('deliveries/', v.deliveries, name='deliveries'),
]
