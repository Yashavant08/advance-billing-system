from django.contrib import admin
from .models import Invoice, InvoiceItem

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'customer', 'distributor', 'subtotal', 'gst_amount', 'total_amount', 'created_at')
    search_fields = ('invoice_number', 'customer__name')
    list_filter = ('created_at',)

@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'product', 'quantity', 'unit_price', 'gst_rate')