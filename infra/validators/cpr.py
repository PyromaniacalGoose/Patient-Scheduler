from django.core.exceptions import ValidationError
import re

def validate_cpr(value):
    if not re.fullmatch(r"\d{10}", value):
        raise ValidationError("CPR number must contain exactly 10 digits.")