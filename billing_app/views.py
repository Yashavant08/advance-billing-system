from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import DistributorProfile, Product, Bill, Customer
import random
import qrcode
from io import BytesIO
from django.core.files import File
from django.contrib.auth import logout
from django.shortcuts import redirect


def register_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        phone = request.POST.get('phone')
        password = request.POST.get('password')
        
        if len(password) < 6:
            messages.error(request, "Password must be at least 6 characters long.")
            return redirect('register')
            
        if User.objects.filter(username=email).exists():
            messages.error(request, "Email already registered!")
            return redirect('register')
            
        user = User.objects.create_user(username=email, email=email, password=password, first_name=name)
        user.save()
        
        DistributorProfile.objects.create(user=user, phone=phone)
        
        messages.success(request, "Registration successful! Please login.")
        return redirect('login')
        
    return render(request, 'billing_app/register.html')


def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        user = authenticate(request, username=email, password=password)
        
        if user is not None:
            login(request, user)
            messages.success(request, "Login successful!")
            return redirect('dashboard')
        else:
            messages.error(request, "Invalid email or password.")
            return redirect('login')
            
    return render(request, 'billing_app/login.html')


@login_required(login_url='login')
def dashboard_view(request):
    return render(request, 'billing_app/dashboard.html')

@login_required(login_url='login')
def add_product_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        category = request.POST.get('category')
        price = request.POST.get('price')
        stock = request.POST.get('stock')
        gst_rate = request.POST.get('gst_rate', 0.00)
        
        # Validation checks
        try:
            price_val = float(price)
            stock_val = int(stock)
            gst_val = float(gst_rate)
            
            if price_val <= 0:
                messages.error(request, "Price must be greater than 0.")
                return redirect('add_product')
                
            if stock_val < 0:
                messages.error(request, "Stock cannot be negative.")
                return redirect('add_product')
                
        except ValueError:
            messages.error(request, "Invalid input for price or stock.")
            return redirect('add_product')

        # Product save karo
        Product.objects.create(
            distributor=request.user,
            name=name,
            category=category,
            price=price_val,
            stock=stock_val,
            gst_rate=gst_val
        )
        messages.success(request, "Product added successfully!")
        return redirect('dashboard')
        
    return render(request, 'billing_app/add_product.html')

@login_required(login_url='login')
def create_bill_view(request):
    if request.method == 'POST':
        customer_name = request.POST.get('customer_name')
        customer_phone = request.POST.get('customer_phone')
        customer_address = request.POST.get('customer_address') # Naya Address field
        total_amount = request.POST.get('total_amount')
        
        # 1. Bill Database mein save karo
        bill = Bill.objects.create(
            distributor=request.user,
            customer_name=customer_name,
            customer_phone=customer_phone,
            total_amount=total_amount
        )
        
        # Customer table mein save/update karo (Address ke sath)
        customer, created = Customer.objects.get_or_create(
            distributor=request.user,
            phone=customer_phone,
            defaults={'name': customer_name, 'address': customer_address}
        )
        if not created and customer_address:
            customer.name = customer_name
            customer.address = customer_address
            customer.save()
        
        # 2. QR Code Data generate karo
        qr_data = f"Bill ID: {bill.id} | Customer: {customer_name} | Amount: ₹{total_amount}"
        
        # 3. qrcode library se image banao
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill='black', back_color='white')
        
        # 4. Image ko Django ke ImageField mein save karo
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        file_name = f'qr_bill_{bill.id}.png'
        bill.qr_code.save(file_name, File(buffer), save=True)
        
        messages.success(request, "Bill and QR Code generated successfully!")
        return redirect('bill_detail', bill_id=bill.id)
        
    return render(request, 'billing_app/create_bill.html')

@login_required(login_url='login')
def bill_detail_view(request, bill_id):
    bill = Bill.objects.get(id=bill_id, distributor=request.user)
    return render(request, 'billing_app/bill_detail.html', {'bill': bill})


def generate_otp_view(request):
    if request.method == 'POST':
        phone = request.POST.get('phone')
        try:
            profile = DistributorProfile.objects.get(phone=phone)
            otp_code = str(random.randint(100000, 999999))
            profile.otp = otp_code
            profile.save()
            
            print(f"--- OTP FOR {phone}: {otp_code} ---")
            
            messages.success(request, f"OTP sent successfully! (Check console for testing: {otp_code})")
            return redirect('verify_otp')
        except DistributorProfile.DoesNotExist:
            messages.error(request, "Phone number not found!")
            
    return render(request, 'billing_app/send_otp.html')


def verify_otp_view(request):
    if request.method == 'POST':
        entered_otp = request.POST.get('otp')
        
        try:
            profile = DistributorProfile.objects.get(otp=entered_otp)
            profile.is_verified = True
            profile.otp = None 
            profile.save()
            
            messages.success(request, "Account verified successfully! Please login.")
            return redirect('login')
        except DistributorProfile.DoesNotExist:
            messages.error(request, "Invalid OTP. Please try again.")
            
    return render(request, 'billing_app/verify_otp.html')

@login_required(login_url='login')
def order_history_view(request):
    # Logged-in distributor ke banaye gaye saare bills fetch karo
    bills = Bill.objects.filter(distributor=request.user).order_by('-created_at')
    return render(request, 'billing_app/order_history.html', {'bills': bills})

@login_required(login_url='login')
def customer_list_view(request):
    query = request.GET.get('q', '')
    if query:
        customers = Customer.objects.filter(distributor=request.user, name__icontains=query) | Customer.objects.filter(distributor=request.user, phone__icontains=query)
    else:
        customers = Customer.objects.filter(distributor=request.user).order_by('-created_at')
    return render(request, 'billing_app/customer_list.html', {'customers': customers, 'query': query})

@login_required(login_url='login')
def update_customer_view(request, pk):
    customer = Customer.objects.get(pk=pk, distributor=request.user)
    if request.method == 'POST':
        customer.name = request.POST.get('name')
        customer.email = request.POST.get('email')
        customer.phone = request.POST.get('phone')
        customer.address = request.POST.get('address')
        customer.save()
        messages.success(request, "Customer updated successfully!")
        return redirect('customer_list')
    return render(request, 'billing_app/update_customer.html', {'customer': customer})

@login_required(login_url='login')
def delete_customer_view(request, pk):
    customer = Customer.objects.get(pk=pk, distributor=request.user)
    customer.delete()
    messages.success(request, "Customer deleted successfully!")
    return redirect('customer_list')


def logout_view(request):
    logout(request)
    return redirect('login') 

from django.core.paginator import Paginator

@login_required(login_url='login')
def product_list_view(request):
    query = request.GET.get('q', '')
    
    if query:
        products = Product.objects.filter(distributor=request.user, name__icontains=query).order_by('-created_at')
    else:
        products = Product.objects.filter(distributor=request.user).order_by('-created_at')
        
    # Pagination (10 products per page)
    paginator = Paginator(products, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'billing_app/product_list.html', {'page_obj': page_obj, 'query': query})