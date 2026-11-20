from decimal import Decimal
import uuid
from django import forms
from .models import Customer, Invoice, Product


class ProductSelectWidget(forms.Select):
    """Custom Select widget that attaches price and gst_rate data attributes to options."""

    def __init__(self, attrs=None, choices=(), products_map=None):
        super().__init__(attrs=attrs, choices=choices)
        self.products_map = products_map or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        product_id = getattr(value, 'value', value)
        try:
            prod_id_int = int(product_id)
        except (TypeError, ValueError):
            prod_id_int = None

        if prod_id_int and prod_id_int in self.products_map:
            prod = self.products_map[prod_id_int]
            option['attrs']['data-price'] = str(prod.price)
            option['attrs']['data-gst'] = str(prod.gst_rate)
        return option


class InvoiceCreateForm(forms.ModelForm):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.none(),
        empty_label="-- Select Customer --",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_customer', 'required': True}),
    )
    invoice_number = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_invoice_number',
            'placeholder': 'Auto-generated if left blank',
        }),
    )

    class Meta:
        model = Invoice
        fields = ['customer', 'invoice_number']

    def __init__(self, *args, **kwargs):
        distributor = kwargs.pop('distributor', None)
        super().__init__(*args, **kwargs)
        if distributor is not None:
            self.fields['customer'].queryset = Customer.objects.filter(
                distributor=distributor
            ).order_by('name')
        if not self.initial.get('invoice_number'):
            self.initial['invoice_number'] = f"INV-{uuid.uuid4().hex[:8].upper()}"

    def clean_invoice_number(self):
        inv_num = (self.cleaned_data.get('invoice_number') or '').strip()
        if not inv_num:
            return f"INV-{uuid.uuid4().hex[:8].upper()}"
        return inv_num


class InvoiceItemForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        empty_label="-- Select Product --",
    )
    quantity = forms.IntegerField(
        min_value=1,
        initial=1,
        widget=forms.NumberInput(attrs={
            'class': 'form-control item-qty',
            'min': '1',
            'value': '1',
            'required': True,
        }),
    )
    gst_rate = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal('0.00'),
        max_value=Decimal('100.00'),
        initial=Decimal('0.00'),
        widget=forms.NumberInput(attrs={
            'class': 'form-control item-gst',
            'step': '0.01',
            'min': '0',
            'max': '100',
            'value': '0.00',
        }),
    )
    discount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0.00'),
        initial=Decimal('0.00'),
        required=False,
        widget=forms.NumberInput(attrs={
            'class': 'form-control item-discount',
            'step': '0.01',
            'min': '0',
            'value': '0.00',
        }),
    )

    def __init__(self, *args, **kwargs):
        distributor = kwargs.pop('distributor', None)
        super().__init__(*args, **kwargs)
        products_qs = Product.objects.none()
        if distributor is not None:
            products_qs = Product.objects.filter(distributor=distributor).order_by('name')

        products_map = {p.id: p for p in products_qs}
        self.fields['product'].queryset = products_qs
        self.fields['product'].widget = ProductSelectWidget(
            attrs={'class': 'form-select item-product', 'required': True},
            products_map=products_map,
        )