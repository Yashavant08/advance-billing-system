import base64
from decimal import Decimal, InvalidOperation
from io import BytesIO
import random

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
import qrcode

from .models import Customer, DistributorProfile, Invoice, InvoiceItem, Product


def register_view(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '')

        if len(password) < 6:
            messages.error(request, "Password must be at least 6 characters long.")
            return redirect('register')

        if User.objects.filter(username=email).exists():
            messages.error(request, "Email already registered!")
            return redirect('register')

        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            first_name=name,
        )
        DistributorProfile.objects.create(user=user, phone=phone)

        messages.success(request, "Registration successful! Please login.")
        return redirect('login')

    return render(request, 'billing_app/register.html')


def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')

        matched_user = (
            User.objects.filter(username__iexact=email).first()
            or User.objects.filter(email__iexact=email).first()
        )
        auth_username = matched_user.username if matched_user else email

        user = authenticate(request, username=auth_username, password=password)

        if user is not None:
            login(request, user)
            messages.success(request, "Login successful!")
            return redirect('dashboard')
        else:
            messages.error(request, "Invalid email or password.")
            return redirect('login')

    return render(request, 'billing_app/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required(login_url='login')
def dashboard_view(request):
    return render(request, 'billing_app/dashboard.html')


@login_required(login_url='login')
def profile_view(request):
    profile, _ = DistributorProfile.objects.get_or_create(user=request.user)

    form_data = {
        'name': request.user.first_name,
        'email': request.user.email,
        'phone': profile.phone or '',
    }

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()

        form_data = {
            'name': name,
            'email': email,
            'phone': phone,
        }

        errors = []

        if not name:
            errors.append("Name is required.")

        if not email:
            errors.append("Email is required.")
        else:
            try:
                validate_email(email)
            except ValidationError:
                errors.append("Please enter a valid email address.")

        if email and User.objects.filter(username=email).exclude(pk=request.user.pk).exists():
            errors.append("This email is already registered with another account.")

        clean_phone = ''.join(ch for ch in phone if ch.isdigit() or ch == '+')
        digits_only = ''.join(ch for ch in clean_phone if ch.isdigit())
        if len(digits_only) < 10 or len(digits_only) > 15:
            errors.append("Please enter a valid phone number (10 to 15 digits).")

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            request.user.first_name = name
            request.user.email = email
            request.user.username = email
            request.user.save()

            profile.phone = clean_phone
            profile.save()

            messages.success(request, "Profile updated successfully!")
            return redirect('profile')

    return render(
        request,
        'billing_app/profile.html',
        {
            'profile': profile,
            'form_data': form_data,
        },
    )


def _issue_otp_for_user(user):
    profile, _ = DistributorProfile.objects.get_or_create(user=user)
    otp_code = f"{random.randint(100000, 999999)}"
    profile.otp = otp_code
    profile.save()
    return otp_code


def generate_otp_view(request):
    email_prefill = ''
    session_user_id = request.session.get('otp_reset_user_id')
    if session_user_id:
        existing_user = User.objects.filter(pk=session_user_id).first()
        if existing_user:
            email_prefill = existing_user.email or existing_user.username

    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        email_prefill = email

        if not email:
            messages.error(request, "Please enter your registered email address.")
            return render(request, 'billing_app/forgot_password.html', {'email': email_prefill})

        user = (
            User.objects.filter(username__iexact=email).first()
            or User.objects.filter(email__iexact=email).first()
        )
        if not user:
            messages.error(request, "No distributor account found with this email.")
            return render(request, 'billing_app/forgot_password.html', {'email': email_prefill})

        otp_code = _issue_otp_for_user(user)
        request.session['otp_reset_user_id'] = user.pk

        print(f"[OTP DEBUG] Reset OTP for {email}: {otp_code}")
        messages.success(request, f"OTP sent to {email}. (Dev OTP: {otp_code})")
        return redirect('verify_otp')

    return render(request, 'billing_app/forgot_password.html', {'email': email_prefill})


def verify_otp_view(request):
    user_id = request.session.get('otp_reset_user_id')
    target_user = User.objects.filter(pk=user_id).first() if user_id else None

    if not target_user:
        messages.error(request, "Session expired or email not requested. Please request an OTP first.")
        return redirect('send_otp')

    profile, _ = DistributorProfile.objects.get_or_create(user=target_user)

    if request.method == 'POST':
        action = request.POST.get('action', 'verify')

        if action == 'resend':
            otp_code = _issue_otp_for_user(target_user)
            print(f"[OTP DEBUG] Resent OTP for {target_user.email}: {otp_code}")
            messages.info(request, f"New OTP sent! (Dev OTP: {otp_code})")
            return redirect('verify_otp')

        entered_otp = request.POST.get('otp', '').strip()
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        if not entered_otp:
            messages.error(request, "Please enter the 6-digit OTP.")
            return render(request, 'billing_app/verify_otp.html', {'user_obj': target_user})

        if not profile.otp or entered_otp != profile.otp:
            messages.error(request, "Invalid OTP. Please check or click Resend OTP.")
            return render(request, 'billing_app/verify_otp.html', {'user_obj': target_user})

        if len(new_password) < 6:
            messages.error(request, "New password must be at least 6 characters long.")
            return render(request, 'billing_app/verify_otp.html', {'user_obj': target_user})

        if new_password != confirm_password:
            messages.error(request, "New password and confirm password do not match.")
            return render(request, 'billing_app/verify_otp.html', {'user_obj': target_user})

        target_user.set_password(new_password)
        target_user.save()

        profile.otp = None
        profile.is_verified = True
        profile.save()

        request.session.pop('otp_reset_user_id', None)
        messages.success(request, "OTP verified and password reset successfully! Please login.")
        return redirect('login')

    return render(request, 'billing_app/verify_otp.html', {'user_obj': target_user})


def _parse_product_inputs(request):
    name = request.POST.get('name', '').strip()
    category = request.POST.get('category', '').strip()
    price_raw = request.POST.get('price', '').strip()
    stock_raw = request.POST.get('stock', '').strip()
    gst_raw = request.POST.get('gst_rate', '0').strip()

    errors = []
    if not name:
        errors.append("Product name is required.")

    try:
        price = Decimal(price_raw)
        if price <= Decimal('0'):
            errors.append("Price must be greater than 0.")
    except (InvalidOperation, ValueError):
        price = None
        errors.append("Enter a valid numeric price.")

    try:
        stock = int(stock_raw)
        if stock < 0:
            errors.append("Stock cannot be negative.")
    except ValueError:
        stock = None
        errors.append("Enter a valid whole number for stock.")

    try:
        gst_rate = Decimal(gst_raw if gst_raw != '' else '0')
        if gst_rate < Decimal('0') or gst_rate > Decimal('100'):
            errors.append("GST rate must be between 0 and 100.")
    except (InvalidOperation, ValueError):
        gst_rate = Decimal('0.00')
        errors.append("Enter a valid GST rate.")

    return {
        'name': name,
        'category': category,
        'price': price,
        'stock': stock,
        'gst_rate': gst_rate,
    }, errors


@login_required(login_url='login')
def product_list_view(request):
    query = request.GET.get('q', '').strip()
    products_qs = Product.objects.filter(distributor=request.user).order_by('-created_at', '-id')

    if query:
        products_qs = products_qs.filter(
            Q(name__icontains=query) | Q(category__icontains=query)
        )

    paginator = Paginator(products_qs, 8)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        'billing_app/product_list.html',
        {
            'page_obj': page_obj,
            'products': page_obj.object_list,
            'query': query,
        },
    )


@login_required(login_url='login')
def add_product_view(request):
    form_data = {'name': '', 'category': '', 'price': '', 'stock': '', 'gst_rate': '0.00'}

    if request.method == 'POST':
        parsed, errors = _parse_product_inputs(request)
        form_data = {
            'name': parsed['name'],
            'category': parsed['category'],
            'price': request.POST.get('price', ''),
            'stock': request.POST.get('stock', ''),
            'gst_rate': request.POST.get('gst_rate', '0.00'),
        }

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            Product.objects.create(
                distributor=request.user,
                name=parsed['name'],
                category=parsed['category'],
                price=parsed['price'],
                stock=parsed['stock'],
                gst_rate=parsed['gst_rate'],
            )
            messages.success(request, f"Product '{parsed['name']}' added successfully.")
            return redirect('product_list')

    return render(
        request,
        'billing_app/product_form.html',
        {'form_data': form_data, 'mode': 'add'},
    )


@login_required(login_url='login')
def update_product(request, pk):
    product = get_object_or_404(Product, pk=pk, distributor=request.user)
    form_data = {
        'name': product.name,
        'category': product.category,
        'price': product.price,
        'stock': product.stock,
        'gst_rate': product.gst_rate,
    }

    if request.method == 'POST':
        parsed, errors = _parse_product_inputs(request)
        form_data = {
            'name': parsed['name'],
            'category': parsed['category'],
            'price': request.POST.get('price', ''),
            'stock': request.POST.get('stock', ''),
            'gst_rate': request.POST.get('gst_rate', '0.00'),
        }

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            product.name = parsed['name']
            product.category = parsed['category']
            product.price = parsed['price']
            product.stock = parsed['stock']
            product.gst_rate = parsed['gst_rate']
            product.save()
            messages.success(request, f"Product '{product.name}' updated successfully.")
            return redirect('product_list')

    return render(
        request,
        'billing_app/product_form.html',
        {'form_data': form_data, 'product': product, 'mode': 'edit'},
    )


@login_required(login_url='login')
def delete_product(request, pk):
    product = get_object_or_404(Product, pk=pk, distributor=request.user)
    if request.method == 'POST':
        product_name = product.name
        try:
            product.delete()
            messages.success(request, f"Product '{product_name}' deleted successfully.")
        except ProtectedError:
            messages.error(
                request,
                f"Cannot delete '{product_name}' because it is already used in existing invoices.",
            )
    return redirect('product_list')


@login_required(login_url='login')
def customer_list_view(request):
    query = request.GET.get('q', '')
    if query:
        customers = (
            Customer.objects.filter(distributor=request.user, name__icontains=query)
            | Customer.objects.filter(distributor=request.user, phone__icontains=query)
        )
    else:
        customers = Customer.objects.filter(distributor=request.user).order_by('-created_at')
    return render(request, 'billing_app/customer_list.html', {'customers': customers, 'query': query})


@login_required(login_url='login')
def update_customer_view(request, pk):
    customer = get_object_or_404(Customer, pk=pk, distributor=request.user)
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
    customer = get_object_or_404(Customer, pk=pk, distributor=request.user)
    if request.method == 'POST':
        customer.delete()
        messages.success(request, "Customer deleted successfully!")
    return redirect('customer_list')


@login_required(login_url='login')
@transaction.atomic
def create_bill_view(request):
    products = Product.objects.filter(distributor=request.user, stock__gt=0).order_by('name')

    if request.method == 'POST':
        customer_name = request.POST.get('customer_name', '').strip()
        customer_phone = request.POST.get('customer_phone', '').strip()
        customer_address = request.POST.get('customer_address', '').strip()

        product_ids = request.POST.getlist('product_id')
        quantities = request.POST.getlist('quantity')

        if not customer_name or not customer_phone:
            messages.error(request, "Customer name and phone number are required.")
            return redirect('create_bill')

        if not product_ids:
            messages.error(request, "Please select at least one product.")
            return redirect('create_bill')

        customer, created = Customer.objects.get_or_create(
            distributor=request.user,
            phone=customer_phone,
            defaults={'name': customer_name, 'address': customer_address},
        )
        if not created:
            customer.name = customer_name
            if customer_address:
                customer.address = customer_address
            customer.save()

        inv_number = f"INV-{timezone.now().strftime('%Y%m%d%H%M%S')}"
        invoice = Invoice.objects.create(
            distributor=request.user,
            customer=customer,
            invoice_number=inv_number,
        )

        subtotal_sum = Decimal('0.00')
        gst_sum = Decimal('0.00')
        items_created = 0

        for prod_id, qty_raw in zip(product_ids, quantities):
            if not prod_id:
                continue
            try:
                qty = int(qty_raw)
                if qty <= 0:
                    continue
                product = Product.objects.select_for_update().get(
                    pk=int(prod_id),
                    distributor=request.user,
                )
            except (ValueError, Product.DoesNotExist):
                continue

            if qty > product.stock:
                transaction.set_rollback(True)
                messages.error(
                    request,
                    f'Insufficient stock for "{product.name}" (Available: {product.stock}).',
                )
                return redirect('create_bill')

            unit_price = Decimal(str(product.price))
            gst_rate = Decimal(str(product.gst_rate))
            line_sub = unit_price * qty
            line_gst = (line_sub * gst_rate) / Decimal('100')

            InvoiceItem.objects.create(
                invoice=invoice,
                product=product,
                quantity=qty,
                unit_price=unit_price,
                gst_rate=gst_rate,
            )

            product.stock -= qty
            product.save()

            subtotal_sum += line_sub
            gst_sum += line_gst
            items_created += 1

        if items_created == 0:
            transaction.set_rollback(True)
            messages.error(request, "Please enter a valid quantity for at least one product.")
            return redirect('create_bill')

        invoice.subtotal = subtotal_sum.quantize(Decimal('0.01'))
        invoice.gst_amount = gst_sum.quantize(Decimal('0.01'))
        invoice.total_amount = (subtotal_sum + gst_sum).quantize(Decimal('0.01'))
        invoice.save()

        messages.success(request, f"Invoice #{invoice.invoice_number} created successfully!")
        return redirect('bill_detail', bill_id=invoice.pk)

    return render(request, 'billing_app/create_bill.html', {'products': products})


@login_required(login_url='login')
def bill_detail_view(request, bill_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related('customer', 'distributor').prefetch_related('items__product'),
        pk=bill_id,
        distributor=request.user,
    )

    items_list = list(invoice.items.all())
    inv_label = invoice.invoice_number or f"Bill #{invoice.pk}"

    qr_lines = [
        "ADVANCE BILLING SYSTEM",
        f"Invoice: {inv_label}",
        f"Date: {invoice.created_at.strftime('%d %b %Y, %I:%M %p')}",
        f"Customer: {invoice.customer.name}",
        f"Phone: {invoice.customer.phone}",
    ]
    if invoice.customer.address:
        qr_lines.append(f"Address: {invoice.customer.address}")

    qr_lines.append("Items:")
    for item in items_list:
        unit_price = item.unit_price if getattr(item, 'unit_price', None) is not None else item.product.price
        line_amt = (
            item.line_total
            if getattr(item, 'line_total', None) is not None
            else item.total_amount
            if getattr(item, 'total_amount', None) is not None
            else ""
        )
        line_str = f"- {item.product.name} x{item.quantity} @ Rs.{unit_price}"
        if line_amt != "":
            line_str += f" = Rs.{line_amt}"
        qr_lines.append(line_str)

    if getattr(invoice, 'subtotal', None) is not None:
        qr_lines.append(f"Subtotal: Rs.{invoice.subtotal}")
    if getattr(invoice, 'gst_amount', None) is not None:
        qr_lines.append(f"GST: Rs.{invoice.gst_amount}")
    qr_lines.append(f"Total Amount: Rs.{invoice.total_amount}")

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=3,
    )
    qr.add_data("\n".join(qr_lines))
    qr.make(fit=True)

    img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    qr_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return render(
        request,
        'billing_app/bill_detail.html',
        {
            'invoice': invoice,
            'qr_base64': qr_base64,
        },
    )


@login_required(login_url='login')
def order_history_view(request):
    invoices = (
        Invoice.objects.filter(distributor=request.user)
        .select_related('customer')
        .prefetch_related('items__product')
        .order_by('-created_at')
    )
    return render(request, 'billing_app/order_history.html', {'invoices': invoices})

from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from .forms import InvoiceCreateForm, InvoiceItemForm
from .models import InvoiceItem, Product


@login_required
def create_invoice(request):
    distributor = request.user

    if request.method == 'POST':
        form = InvoiceCreateForm(request.POST, distributor=distributor)
        product_ids = request.POST.getlist('product')
        quantities = request.POST.getlist('quantity')
        gst_rates = request.POST.getlist('gst_rate')
        discounts = request.POST.getlist('discount')

        if not product_ids:
            messages.error(request, "Please add at least one product item to the invoice.")
        elif form.is_valid():
            items_to_create = []
            subtotal_sum = Decimal('0.00')
            discount_sum = Decimal('0.00')
            gst_sum = Decimal('0.00')
            grand_total_sum = Decimal('0.00')
            has_item_error = False

            for idx, prod_id in enumerate(product_ids):
                if not prod_id:
                    continue

                try:
                    product = Product.objects.get(id=int(prod_id), distributor=distributor)
                except (ValueError, Product.DoesNotExist):
                    messages.error(request, f"Invalid product selected in row {idx + 1}.")
                    has_item_error = True
                    break

                try:
                    qty = int(quantities[idx]) if idx < len(quantities) else 1
                    if qty < 1:
                        raise ValueError("Quantity must be at least 1.")
                except (ValueError, TypeError):
                    messages.error(request, f"Invalid quantity in row {idx + 1}.")
                    has_item_error = True
                    break

                try:
                    raw_gst = gst_rates[idx] if idx < len(gst_rates) and gst_rates[idx] != '' else product.gst_rate
                    gst_rate = Decimal(str(raw_gst))
                    if gst_rate < Decimal('0.00') or gst_rate > Decimal('100.00'):
                        raise InvalidOperation()
                except (InvalidOperation, ValueError, TypeError):
                    messages.error(request, f"Invalid GST rate in row {idx + 1}.")
                    has_item_error = True
                    break

                try:
                    raw_disc = discounts[idx] if idx < len(discounts) and discounts[idx] != '' else '0.00'
                    disc = Decimal(str(raw_disc))
                    if disc < Decimal('0.00'):
                        raise InvalidOperation()
                except (InvalidOperation, ValueError, TypeError):
                    messages.error(request, f"Invalid discount in row {idx + 1}.")
                    has_item_error = True
                    break

                line_sub = (product.price * qty).quantize(Decimal('0.01'))
                line_disc = min(disc, line_sub).quantize(Decimal('0.01'))
                taxable = (line_sub - line_disc).quantize(Decimal('0.01'))
                line_gst = ((taxable * gst_rate) / Decimal('100')).quantize(Decimal('0.01'))
                line_tot = (taxable + line_gst).quantize(Decimal('0.01'))

                subtotal_sum += line_sub
                discount_sum += line_disc
                gst_sum += line_gst
                grand_total_sum += line_tot

                items_to_create.append({
                    'product': product,
                    'quantity': qty,
                    'unit_price': product.price,
                    'discount': line_disc,
                    'gst_rate': gst_rate,
                })

            if not has_item_error and not items_to_create:
                messages.error(request, "Please select at least one valid product.")
                has_item_error = True

            if not has_item_error:
                with transaction.atomic():
                    invoice = form.save(commit=False)
                    invoice.distributor = distributor
                    invoice.subtotal = subtotal_sum.quantize(Decimal('0.01'))
                    invoice.discount_amount = discount_sum.quantize(Decimal('0.01'))
                    invoice.gst_amount = gst_sum.quantize(Decimal('0.01'))
                    invoice.total_amount = grand_total_sum.quantize(Decimal('0.01'))
                    invoice.save()

                    for item in items_to_create:
                        InvoiceItem.objects.create(
                            invoice=invoice,
                            product=item['product'],
                            quantity=item['quantity'],
                            unit_price=item['unit_price'],
                            discount=item['discount'],
                            gst_rate=item['gst_rate'],
                        )

                messages.success(request, f"Invoice #{invoice.invoice_number} created successfully!")
                return redirect('create_invoice')
    else:
        form = InvoiceCreateForm(distributor=distributor)

    item_form = InvoiceItemForm(distributor=distributor)
    products = Product.objects.filter(distributor=distributor).order_by('name')

    return render(request, 'billing_app/create_invoice.html', {
        'form': form,
        'item_form': item_form,
        'products': products,
    })

from io import BytesIO
from django.http import HttpResponse
from django.template.loader import get_template
from xhtml2pdf import pisa


def invoice_pdf_view(request, invoice_id):
    invoice = ...  # Invoice + items fetch karo
    template = get_template('billing/invoice_pdf.html')
    html = template.render({'invoice': invoice})

    result = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)

    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="invoice_{invoice.id}.pdf"'
        return response
    return HttpResponse("Error generating PDF", status=500)
from decimal import Decimal, InvalidOperation
from io import BytesIO
import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from xhtml2pdf import pisa

from .models import Customer, Invoice, InvoiceItem, Product


def _generate_invoice_number():
    return f"INV-{int(time.time())}"


@login_required
def create_invoice(request):
    customers = Customer.objects.filter(distributor=request.user).order_by('name')
    products = Product.objects.filter(distributor=request.user).order_by('name')

    if request.method == 'POST':
        customer_id = request.POST.get('customer')
        invoice_number = (request.POST.get('invoice_number') or '').strip() or _generate_invoice_number()

        product_ids = request.POST.getlist('product_id[]')
        quantities = request.POST.getlist('quantity[]')
        discounts = request.POST.getlist('discount[]')

        customer = get_object_or_404(Customer, id=customer_id, distributor=request.user)

        if not product_ids:
            messages.error(request, "Kam se kam ek product add karo.")
            return render(request, 'billing/create_invoice.html', {
                'customers': customers,
                'products': products,
            })

        try:
            with transaction.atomic():
                invoice = Invoice.objects.create(
                    distributor=request.user,
                    customer=customer,
                    invoice_number=invoice_number,
                )

                subtotal_sum = Decimal('0.00')
                discount_sum = Decimal('0.00')
                gst_sum = Decimal('0.00')
                total_sum = Decimal('0.00')
                created_any = False

                for idx, prod_id in enumerate(product_ids):
                    if not prod_id:
                        continue

                    qty = int(quantities[idx]) if idx < len(quantities) and quantities[idx] else 0
                    if qty <= 0:
                        continue

                    raw_disc = discounts[idx] if idx < len(discounts) and discounts[idx] else '0.00'
                    disc = Decimal(raw_disc)
                    if disc < Decimal('0.00'):
                        disc = Decimal('0.00')

                    product = Product.objects.select_for_update().get(
                        id=prod_id,
                        distributor=request.user,
                    )

                    if product.stock < qty:
                        raise ValueError(f"'{product.name}' ka sirf {product.stock} stock available hai.")

                    product.stock = F('stock') - qty
                    product.save(update_fields=['stock'])

                    unit_price = product.price
                    gst_rate = product.gst_rate

                    line_sub = unit_price * qty
                    line_taxable = line_sub - disc if (line_sub - disc) > Decimal('0.00') else Decimal('0.00')
                    line_gst = (line_taxable * gst_rate) / Decimal('100')
                    line_tot = line_taxable + line_gst

                    InvoiceItem.objects.create(
                        invoice=invoice,
                        product=product,
                        quantity=qty,
                        unit_price=unit_price,
                        discount=disc,
                        gst_rate=gst_rate,
                    )

                    subtotal_sum += line_sub
                    discount_sum += disc
                    gst_sum += line_gst
                    total_sum += line_tot
                    created_any = True

                if not created_any:
                    raise ValueError("Valid quantity ke saath kam se kam ek item chuno.")

                invoice.subtotal = subtotal_sum
                invoice.discount_amount = discount_sum
                invoice.gst_amount = gst_sum
                invoice.total_amount = total_sum
                invoice.save(update_fields=['subtotal', 'discount_amount', 'gst_amount', 'total_amount'])

            messages.success(
                request,
                f"Invoice #{invoice.invoice_number} successfully save ho gaya! Total: ₹{invoice.total_amount:.2f}"
            )
            return redirect('invoice_detail', invoice_id=invoice.id)

        except (ValueError, InvalidOperation) as exc:
            messages.error(request, str(exc))

    return render(request, 'billing/create_invoice.html', {
        'customers': customers,
        'products': products,
    })

@login_required
def invoice_pdf(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related('customer', 'distributor').prefetch_related('items__product'),
        id=invoice_id,
        distributor=request.user,
    )

    template = get_template('billing_app/invoice_pdf.html')
    html = template.render({
        'invoice': invoice,
        'items': invoice.items.all(),
        'distributor': invoice.distributor,
        'customer': invoice.customer,
    })

    pdf_buffer = BytesIO()
    pisa_status = pisa.pisaDocument(BytesIO(html.encode('UTF-8')), pdf_buffer)

    if pisa_status.err:
        return HttpResponse("PDF generate karne mein error aaya.", status=500)

    response = HttpResponse(pdf_buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="invoice_{invoice.invoice_number or invoice.pk}.pdf"'
    return response
@login_required
def invoice_detail_view(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related('customer', 'distributor').prefetch_related('items__product'),
        id=invoice_id,
        distributor=request.user,
    )
    return render(request, 'billing/invoice_detail.html', {
        'invoice': invoice,
        'items': invoice.items.all(),
        'distributor': invoice.distributor,
        'customer': invoice.customer,
    })