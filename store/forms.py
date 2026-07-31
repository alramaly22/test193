"""Checkout form.

The previous checkout read straight from ``request.POST`` with no validation,
so a blank name or a nonsense phone number produced an unusable order that
nobody could deliver. Everything is validated here instead, and the form is
re-rendered with errors and the customer's input intact on failure.
"""

import re

from django import forms

from .models import Order

# Egypt's 27 governorates. A select rather than a free text field: typo-free
# values make delivery zones and shipping reports possible later.
GOVERNORATES = [
    "Cairo",
    "Giza",
    "Alexandria",
    "Dakahlia",
    "Red Sea",
    "Beheira",
    "Fayoum",
    "Gharbia",
    "Ismailia",
    "Menofia",
    "Minya",
    "Qalyubia",
    "New Valley",
    "Suez",
    "Aswan",
    "Assiut",
    "Beni Suef",
    "Port Said",
    "Damietta",
    "Sharkia",
    "South Sinai",
    "Kafr El Sheikh",
    "Matrouh",
    "Luxor",
    "Qena",
    "North Sinai",
    "Sohag",
]

# Accepts 01XXXXXXXXX, 201XXXXXXXXX, +201XXXXXXXXX and also allows other
# international numbers so customers outside Egypt are not locked out.
EGYPT_MOBILE = re.compile(r"^(?:\+?20)?1[0125]\d{8}$")
GENERIC_INTERNATIONAL = re.compile(r"^\+?\d{8,15}$")


class CheckoutForm(forms.ModelForm):
    payment = forms.ChoiceField(
        choices=Order.PaymentMethod.choices,
        initial=Order.PaymentMethod.CASH,
        widget=forms.RadioSelect,
        error_messages={"required": "Choose how you would like to pay."},
    )

    class Meta:
        model = Order
        fields = [
            "full_name",
            "phone",
            "email",
            "governorate",
            "city",
            "address",
            "notes",
        ]
        widgets = {
            "full_name": forms.TextInput(
                attrs={"placeholder": "Your full name", "autocomplete": "name"}
            ),
            "phone": forms.TextInput(
                attrs={
                    "placeholder": "01xxxxxxxxx",
                    "inputmode": "tel",
                    "autocomplete": "tel",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "placeholder": "you@example.com",
                    "autocomplete": "email",
                }
            ),
            "governorate": forms.Select(
                choices=[("", "Select your governorate")]
                + [(g, g) for g in GOVERNORATES]
            ),
            "city": forms.TextInput(
                attrs={"placeholder": "City or district", "autocomplete": "address-level2"}
            ),
            "address": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Street, building, floor, apartment",
                    "autocomplete": "street-address",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "Landmark, preferred delivery time, anything else",
                }
            ),
        }
        labels = {
            "full_name": "Full name",
            "phone": "Phone number",
            "email": "Email",
            "governorate": "Governorate",
            "city": "City",
            "address": "Address",
            "notes": "Delivery notes",
        }
        error_messages = {
            "full_name": {"required": "We need a name for the delivery."},
            "phone": {"required": "We need a phone number to arrange delivery."},
            "governorate": {"required": "Select your governorate."},
            "city": {"required": "Enter your city."},
            "address": {"required": "Enter the full address."},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Email is optional on the model, but online payment needs a receipt
        # address, so it becomes conditionally required in clean().
        self.fields["email"].required = False
        self.fields["notes"].required = False

    def clean_full_name(self):
        name = " ".join((self.cleaned_data.get("full_name") or "").split())
        if len(name) < 3:
            raise forms.ValidationError("Enter your full name.")
        if not re.search(r"[^\W\d_]", name, flags=re.UNICODE):
            raise forms.ValidationError("Enter your name in letters.")
        return name

    def clean_phone(self):
        raw = (self.cleaned_data.get("phone") or "").strip()
        compact = re.sub(r"[\s\-()]", "", raw)

        if EGYPT_MOBILE.match(compact):
            digits = re.sub(r"\D", "", compact)
            # Store one canonical local format so staff see consistent numbers.
            if digits.startswith("20"):
                digits = digits[2:]
            return f"0{digits}" if not digits.startswith("0") else digits

        if GENERIC_INTERNATIONAL.match(compact):
            return compact

        raise forms.ValidationError(
            "Enter a valid mobile number, for example 01012345678."
        )

    def clean_city(self):
        city = " ".join((self.cleaned_data.get("city") or "").split())
        if len(city) < 2:
            raise forms.ValidationError("Enter your city.")
        return city

    def clean_address(self):
        address = " ".join((self.cleaned_data.get("address") or "").split())
        if len(address) < 10:
            raise forms.ValidationError(
                "Add a little more detail so the courier can find you."
            )
        return address

    def clean_governorate(self):
        governorate = (self.cleaned_data.get("governorate") or "").strip()
        if governorate not in GOVERNORATES:
            raise forms.ValidationError("Select your governorate from the list.")
        return governorate

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("payment") == Order.PaymentMethod.ONLINE and not cleaned.get(
            "email"
        ):
            self.add_error(
                "email",
                "Add your email so the payment provider can send your receipt.",
            )
        return cleaned
