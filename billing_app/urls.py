from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views

urlpatterns = [
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('add-product/', views.add_product_view, name='add_product'),
    path('create-bill/', views.create_bill_view, name='create_bill'),
    path('send-otp/', views.generate_otp_view, name='send_otp'),
    path('verify-otp/', views.verify_otp_view, name='verify_otp'),
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),
    path('bill/<int:bill_id>/', views.bill_detail_view, name='bill_detail'),
    path('orders/', views.order_history_view, name='order_history'),
]