# Expense Tracking API Documentation

## Overview
A comprehensive REST API for expense tracking with support for categories, tags, advanced filtering, and reporting features.

## Base Information

- **Base URL**: `http://localhost:8000/api/expenses/`
- **Authentication**: HTTP Basic Authentication (Required for all endpoints)
- **Content-Type**: `application/json`
- **Framework**: Django REST Framework

## Authentication

All API endpoints require authentication using HTTP Basic Auth:

```bash
curl -H "Authorization: Basic <base64-encoded-credentials>" \
     http://localhost:8000/api/expenses/expenses/
```

```javascript
// JavaScript fetch with Basic Auth
fetch('http://localhost:8000/api/expenses/expenses/', {
  headers: {
    'Authorization': 'Basic ' + btoa('username:password')
  }
});
```

## Response Formats

### Pagination
All list endpoints return paginated responses:

```json
{
  "count": 100,
  "next": "http://localhost:8000/api/expenses/expenses/?page=2",
  "previous": null,
  "results": [
    // Array of results
  ]
}
```

### Error Responses
Error responses follow this format:

```json
{
  "detail": "Error description",
  "code": "ERROR_CODE"
}
```

Or for validation errors:

```json
{
  "field_name": ["Error message"],
  "another_field": ["Another error message"]
}
```

---

## Categories API

Manage expense categories (Food, Transport, Entertainment, etc.)

### List Categories

**Endpoint**: `GET /api/expenses/categories/`

**Description**: Retrieve all active expense categories with optional filtering by transaction type.

**Query Parameters**:
- `type` (optional): Filter by transaction type (`expense`, `income`, `credit`, `debt`)
- `ordering` (optional): Sort by `name`, `transaction_type`, `created_at`
- `page`: Page number for pagination
- `page_size`: Number of results per page (max 100)

**Request Example**:
```bash
curl -H "Authorization: Basic <credentials>" \
     "http://localhost:8000/api/expenses/categories/?type=expense&ordering=name"
```

```javascript
fetch('http://localhost:8000/api/expenses/categories/?type=expense', {
  headers: {
    'Authorization': 'Basic ' + btoa('username:password')
  }
});
```

**Response Example**:
```json
{
  "count": 25,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 1,
      "name": "Food & Dining",
      "description": "Restaurants, groceries, etc.",
      "color": "#ff6b6b",
      "icon": "restaurant",
      "transaction_type": "expense",
      "is_active": true,
      "created_at": "2024-01-01T10:00:00Z",
      "updated_at": "2024-01-01T10:00:00Z"
    }
  ]
}
```

**Status Codes**:
- `200`: Success
- `401`: Unauthorized

### Create Category

**Endpoint**: `POST /api/expenses/categories/`

**Description**: Create a new expense category.

**Request Body**:
```json
{
  "name": "Entertainment",
  "description": "Movies, games, subscriptions",
  "color": "#4ecdc4",
  "icon": "movie",
  "transaction_type": "expense"
}
```

**Required Fields**:
- `name`: Unique category name (string, max 100 chars)

**Optional Fields**:
- `description`: Category description (string)
- `color`: Hex color code for UI display (string, default: "#007bff")
- `icon`: Icon class or name (string)
- `transaction_type`: Transaction type (string, default: "expense")

**Request Example**:
```bash
curl -X POST \
  -H "Authorization: Basic <credentials>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Entertainment",
    "description": "Movies, games, subscriptions",
    "color": "#4ecdc4",
    "icon": "movie",
    "transaction_type": "expense"
  }' \
  http://localhost:8000/api/expenses/categories/
```

**Response Example**:
```json
{
  "id": 26,
  "name": "Entertainment",
  "description": "Movies, games, subscriptions",
  "color": "#4ecdc4",
  "icon": "movie",
  "transaction_type": "expense",
  "is_active": true,
  "created_at": "2024-01-15T14:30:00Z",
  "updated_at": "2024-01-15T14:30:00Z"
}
```

**Status Codes**:
- `201`: Created successfully
- `400`: Validation error (duplicate name, etc.)
- `401`: Unauthorized

### Retrieve Category

**Endpoint**: `GET /api/expenses/categories/{id}/`

**Description**: Retrieve a specific category by ID.

**Response Example**:
```json
{
  "id": 1,
  "name": "Food & Dining",
  "description": "Restaurants, groceries, etc.",
  "color": "#ff6b6b",
  "icon": "restaurant",
  "transaction_type": "expense",
  "is_active": true,
  "created_at": "2024-01-01T10:00:00Z",
  "updated_at": "2024-01-01T10:00:00Z"
}
```

### Update Category

**Endpoint**: `PUT /api/expenses/categories/{id}/` or `PATCH /api/expenses/categories/{id}/`

**Description**: Update a category completely (PUT) or partially (PATCH).

### Delete Category

**Endpoint**: `DELETE /api/expenses/categories/{id}/`

**Description**: Soft delete a category (sets is_active to false).

---

## Tags API

Manage user-specific expense tags for organization and filtering.

### List Tags

**Endpoint**: `GET /api/expenses/tags/`

**Description**: Retrieve all tags for the authenticated user.

**Query Parameters**:
- `ordering`: Sort by `name`, `created_at` (default: `name`)
- `page`: Page number for pagination
- `page_size`: Number of results per page

**Response Example**:
```json
{
  "count": 10,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 1,
      "name": "urgent",
      "color": "#dc3545",
      "user": 1,
      "created_at": "2024-01-01T10:00:00Z"
    },
    {
      "id": 2,
      "name": "recurring",
      "color": "#28a745",
      "user": 1,
      "created_at": "2024-01-02T10:00:00Z"
    }
  ]
}
```

### Create Tag

**Endpoint**: `POST /api/expenses/tags/`

**Description**: Create a new tag for the authenticated user.

**Request Body**:
```json
{
  "name": "urgent",
  "color": "#dc3545"
}
```

**Required Fields**:
- `name`: Unique tag name for the user (string, max 50 chars)

**Optional Fields**:
- `color`: Hex color code for UI display (string, default: "#6c757d")

**Validation**: Tag names must be unique per user.

### Retrieve, Update, Delete Tag

Similar to categories but user-scoped.

---

## Expenses API

Core expense tracking functionality with comprehensive filtering and search.

### List Expenses

**Endpoint**: `GET /api/expenses/expenses/`

**Description**: Retrieve expenses for the authenticated user with advanced filtering and search.

**Query Parameters**:
- `date_from`: Start date (YYYY-MM-DD)
- `date_to`: End date (YYYY-MM-DD)
- `amount_min`: Minimum amount
- `amount_max`: Maximum amount
- `category`: Category ID
- `tags`: Comma-separated tag IDs
- `transaction_type`: `expense`, `income`, `credit`, `debt`, `repayment`
- `is_recurring`: `true`/`false`
- `search`: Search in description, location, payment method
- `ordering`: `date`, `amount`, `created_at`, `updated_at` (default: `-date`)
- `page`: Page number
- `page_size`: Results per page (max 100)

**Request Examples**:

```bash
# Get expenses for January 2024
curl -H "Authorization: Basic <credentials>" \
     "http://localhost:8000/api/expenses/expenses/?date_from=2024-01-01&date_to=2024-01-31"

# Search expenses containing "coffee"
curl -H "Authorization: Basic <credentials>" \
     "http://localhost:8000/api/expenses/expenses/?search=coffee"

# Get high-value expenses with specific tags
curl -H "Authorization: Basic <credentials>" \
     "http://localhost:8000/api/expenses/expenses/?amount_min=100&tags=1,3&ordering=-amount"
```

```javascript
// Filter expenses by date range and category
const params = new URLSearchParams({
  date_from: '2024-01-01',
  date_to: '2024-01-31',
  category: '5',
  ordering: '-date'
});

fetch(`http://localhost:8000/api/expenses/expenses/?${params}`, {
  headers: {
    'Authorization': 'Basic ' + btoa('username:password')
  }
});
```

**Response Example**:
```json
{
  "count": 150,
  "next": "http://localhost:8000/api/expenses/expenses/?page=2",
  "previous": null,
  "results": [
    {
      "id": 1,
      "amount": "25.50",
      "amount_display": "₹25.50",
      "transaction_type": "expense",
      "category": {
        "id": 1,
        "name": "Food & Dining",
        "color": "#ff6b6b",
        "icon": "restaurant",
        "transaction_type": "expense"
      },
      "description": "Lunch at restaurant",
      "date": "2024-01-15",
      "tags": [
        {
          "id": 1,
          "name": "urgent",
          "color": "#dc3545"
        }
      ],
      "is_recent": true,
      "balance_effect": "-25.50",
      "created_at": "2024-01-15T12:00:00Z",
      "updated_at": "2024-01-15T12:00:00Z"
    }
  ]
}
```

### Create Expense

**Endpoint**: `POST /api/expenses/expenses/`

**Description**: Create a new expense record.

**Request Body**:
```json
{
  "amount": 25.50,
  "transaction_type": "expense",
  "category_id": 1,
  "description": "Lunch at restaurant",
  "date": "2024-01-15",
  "tag_ids": [1, 2],
  "location": "Downtown Restaurant",
  "payment_method": "Credit Card",
  "is_recurring": false
}
```

**Required Fields**:
- `amount`: Expense amount (decimal, > 0)
- `transaction_type`: Transaction type (must match category type)
- `category_id`: Valid category ID
- `description`: Expense description
- `date`: Date in YYYY-MM-DD format

**Optional Fields**:
- `tag_ids`: Array of tag IDs to associate
- `related_expense`: ID for debt/repayment linking
- `lender_borrower`: Name for debt/credit transactions
- `receipt_image`: Image file upload
- `location`: Location where expense occurred
- `payment_method`: Payment method used
- `is_recurring`: Whether this is a recurring expense
- `recurring_interval`: Interval for recurring expenses

**Validation Rules**:
- Amount must be greater than 0
- Category must exist and be active
- Transaction type must match category type
- Tag IDs must belong to the user

### Update and Delete Expense

Standard CRUD operations available.

### Add Tags to Expense

**Endpoint**: `POST /api/expenses/expenses/{id}/add_tags/`

**Description**: Add tags to an existing expense.

**Request Body**:
```json
{
  "tag_ids": [3, 4]
}
```

### Remove Tags from Expense

**Endpoint**: `DELETE /api/expenses/expenses/{id}/remove_tags/`

**Description**: Remove tags from an existing expense.

**Request Body**:
```json
{
  "tag_ids": [1, 2]
}
```

---

## Summary and Reports API

Analytics and reporting endpoints for expense insights.

### Expense Summary

**Endpoint**: `GET /api/expenses/expenses/summary/`

**Description**: Get comprehensive expense statistics with optional date filtering.

**Query Parameters**:
- `date_from`: Start date (YYYY-MM-DD)
- `date_to`: End date (YYYY-MM-DD)

**Request Example**:
```bash
curl -H "Authorization: Basic <credentials>" \
     "http://localhost:8000/api/expenses/expenses/summary/?date_from=2024-01-01&date_to=2024-01-31"
```

**Response Example**:
```json
{
  "total_expenses": "1250.75",
  "total_income": "3000.00",
  "total_debt": "500.00",
  "total_credit": "200.00",
  "net_balance": "1449.25",
  "transaction_count": 45,
  "category_breakdown": {
    "Food & Dining": "450.25",
    "Transportation": "320.50",
    "Entertainment": "180.00",
    "Utilities": "300.00"
  }
}
```

### Recent Expenses

**Endpoint**: `GET /api/expenses/expenses/recent/`

**Description**: Get expenses from the last 7 days.

**Response Example**:
```json
[
  {
    "id": 10,
    "amount": "15.75",
    "amount_display": "₹15.75",
    "transaction_type": "expense",
    "category": {
      "id": 2,
      "name": "Transportation",
      "color": "#17a2b8",
      "icon": "car"
    },
    "description": "Bus fare",
    "date": "2024-01-15",
    "tags": [],
    "is_recent": true,
    "balance_effect": "-15.75",
    "created_at": "2024-01-15T08:30:00Z",
    "updated_at": "2024-01-15T08:30:00Z"
  }
]
```

### Monthly Report

**Endpoint**: `GET /api/expenses/expenses/monthly_report/`

**Description**: Get detailed monthly expense report with daily totals and category breakdowns.

**Query Parameters**:
- `year`: Year (default: current year)
- `month`: Month (default: current month)

**Request Example**:
```bash
curl -H "Authorization: Basic <credentials>" \
     "http://localhost:8000/api/expenses/expenses/monthly_report/?year=2024&month=1"
```

**Response Example**:
```json
{
  "year": 2024,
  "month": 1,
  "daily_totals": [
    {
      "date": "2024-01-01",
      "total": "85.50",
      "count": 3
    },
    {
      "date": "2024-01-02",
      "total": "42.25",
      "count": 2
    }
  ],
  "category_totals": [
    {
      "category__name": "Food & Dining",
      "category__color": "#ff6b6b",
      "total": "450.25",
      "count": 15
    },
    {
      "category__name": "Transportation",
      "category__color": "#17a2b8",
      "total": "320.50",
      "count": 12
    }
  ],
  "total_amount": "1250.75",
  "total_count": 45
}
```

---

## Frontend Integration Notes

### JavaScript Examples

#### Fetching Expenses with Error Handling
```javascript
async function fetchExpenses(filters = {}) {
  try {
    const params = new URLSearchParams(filters);
    const response = await fetch(
      `http://localhost:8000/api/expenses/expenses/?${params}`,
      {
        headers: {
          'Authorization': 'Basic ' + btoa('username:password'),
          'Content-Type': 'application/json'
        }
      }
    );

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error('Error fetching expenses:', error);
    throw error;
  }
}

// Usage
const expenses = await fetchExpenses({
  date_from: '2024-01-01',
  date_to: '2024-01-31',
  category: '1'
});
```

#### Creating an Expense
```javascript
async function createExpense(expenseData) {
  try {
    const response = await fetch(
      'http://localhost:8000/api/expenses/expenses/',
      {
        method: 'POST',
        headers: {
          'Authorization': 'Basic ' + btoa('username:password'),
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(expenseData)
      }
    );

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error creating expense:', error);
    throw error;
  }
}
```

#### Error Handling Pattern
```javascript
function handleApiError(error, operation) {
  if (error.message.includes('401')) {
    // Redirect to login or refresh token
    window.location.href = '/login';
  } else if (error.message.includes('400')) {
    // Show validation errors
    showValidationErrors(error.details);
  } else {
    // Show generic error message
    showError(`Failed to ${operation}. Please try again.`);
  }
}
```

### Data Transformation Tips

#### Converting API Data for UI Display
```javascript
function transformExpenseForUI(apiExpense) {
  return {
    id: apiExpense.id,
    amount: parseFloat(apiExpense.amount),
    displayAmount: apiExpense.amount_display,
    date: new Date(apiExpense.date),
    description: apiExpense.description,
    category: {
      id: apiExpense.category.id,
      name: apiExpense.category.name,
      color: apiExpense.category.color,
      icon: apiExpense.category.icon
    },
    tags: apiExpense.tags.map(tag => ({
      id: tag.id,
      name: tag.name,
      color: tag.color
    })),
    isRecent: apiExpense.is_recent,
    type: apiExpense.transaction_type
  };
}
```

#### Formatting Summary Data for Charts
```javascript
function transformSummaryForCharts(summaryData) {
  return {
    totalExpenses: parseFloat(summaryData.total_expenses),
    totalIncome: parseFloat(summaryData.total_income),
    netBalance: parseFloat(summaryData.net_balance),
    categoryBreakdown: Object.entries(summaryData.category_breakdown).map(
      ([name, amount]) => ({
        name,
        amount: parseFloat(amount)
      })
    )
  };
}
```

---

## Common HTTP Status Codes

- `200`: Success
- `201`: Created successfully
- `204`: No content (successful deletion/update)
- `400`: Bad request (validation errors)
- `401`: Unauthorized (invalid/missing authentication)
- `404`: Not found (resource doesn't exist)
- `405`: Method not allowed
- `429`: Too many requests (rate limiting)

## Rate Limiting

The API implements rate limiting to ensure fair usage. Exceeding limits will return a `429` status code.

## File Upload

For expense receipt images, send as multipart form data:

```bash
curl -X POST \
  -H "Authorization: Basic <credentials>" \
  -F "receipt_image=@receipt.jpg" \
  -F "amount=25.50" \
  -F "category_id=1" \
  -F "description=Lunch receipt" \
  -F "date=2024-01-15" \
  http://localhost:8000/api/expenses/expenses/
```

---

*This API documentation was generated based on the current implementation. For the most up-to-date information, refer to the source code or contact the development team.*