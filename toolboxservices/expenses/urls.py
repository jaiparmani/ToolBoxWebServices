from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ExpenseViewSet, ExpenseCategoryViewSet, ExpenseTagViewSet

# Create a router for API endpoints
router = DefaultRouter()

# Register ViewSets with the router
router.register(r'categories', ExpenseCategoryViewSet, basename='expense-category')
router.register(r'tags', ExpenseTagViewSet, basename='expense-tag')
router.register(r'expenses', ExpenseViewSet, basename='expense')

# URL patterns
urlpatterns = [
     # Include router URLs for REST API endpoints
     path('', include(router.urls)),

     # Additional custom endpoints can be added here if needed
     # For example:
     # path('summary/', ExpenseViewSet.as_view({'get': 'summary'}), name='expense-summary'),
     # path('recent/', ExpenseViewSet.as_view({'get': 'recent'}), name='expense-recent'),
]

# The router provides the following endpoints:
# Categories:
# - GET /api/categories/ - List all categories
# - POST /api/categories/ - Create a new category
# - GET /api/categories/{id}/ - Retrieve a specific category
# - PUT /api/categories/{id}/ - Update a category
# - PATCH /api/categories/{id}/ - Partially update a category
# - DELETE /api/categories/{id}/ - Delete a category

# Tags:
# - GET /api/tags/ - List user's tags
# - POST /api/tags/ - Create a new tag
# - GET /api/tags/{id}/ - Retrieve a specific tag
# - PUT /api/tags/{id}/ - Update a tag
# - PATCH /api/tags/{id}/ - Partially update a tag
# - DELETE /api/tags/{id}/ - Delete a tag

# Expenses:
# - GET /api/expenses/ - List user's expenses (with filtering and pagination)
# - POST /api/expenses/ - Create a new expense
# - GET /api/expenses/{id}/ - Retrieve a specific expense
# - PUT /api/expenses/{id}/ - Update an expense
# - PATCH /api/expenses/{id}/ - Partially update an expense
# - DELETE /api/expenses/{id}/ - Delete an expense

# Custom expense actions:
# - GET /api/expenses/summary/ - Get expense summary statistics
# - GET /api/expenses/recent/ - Get recent expenses (last 7 days)
# - GET /api/expenses/monthly_report/ - Get monthly expense report
# - POST /api/expenses/{id}/add_tags/ - Add tags to an expense
# - DELETE /api/expenses/{id}/remove_tags/ - Remove tags from an expense