from django.contrib import admin

# Register your models here.
from .models import ExpenseCategory, ExpenseTag, Expense

# Register models with the admin site
admin.site.register(ExpenseCategory)
admin.site.register(ExpenseTag)
admin.site.register(Expense)