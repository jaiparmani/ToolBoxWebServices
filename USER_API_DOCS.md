# User Management API Documentation

## API Overview

This API provides comprehensive user management functionality including user registration, profile management, and password operations for the ToolBox Web Services application.

### Base URL
```
http://localhost:8000/api/users/
```

### Authentication Methods

The API supports **session-based authentication**. Once authenticated, include the session cookie in subsequent requests.

**Authentication Requirements:**
- User registration: No authentication required (public access)
- Profile operations: Authentication required
- Password change: Authentication required

### General Usage

All API responses use JSON format. Successful operations return appropriate HTTP status codes (200, 201, 204) while errors return detailed error messages with appropriate status codes (400, 401, 403, 404).

---

## Authentication

### Session Authentication

The API uses Django's session framework for authentication. After successful login, the server sets a session cookie that should be included in subsequent requests.

**Login Process:**
1. Authenticate via separate login endpoint (typically `/api/auth/login/`)
2. Server responds with session cookie
3. Include session cookie in all authenticated requests

**Cookie Requirements:**
- Name: `sessionid`
- HttpOnly: true
- Secure: false (development) / true (production)
- SameSite: Lax

---

## Endpoints

### 1. User Registration
**Endpoint:** `POST /api/users/users/`

Register a new user account. This endpoint is publicly accessible.

#### Request Body
```json
{
  "username": "string (required)",
  "email": "string (required)",
  "first_name": "string (optional)",
  "last_name": "string (optional)",
  "password": "string (required, min 8 characters)",
  "password_confirm": "string (required, must match password)"
}
```

#### Example Request
```bash
curl -X POST http://localhost:8000/api/users/users/ \
  -H "Content-Type: application/json" \
  -d '{
    "username": "johndoe",
    "email": "john.doe@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "password": "securepassword123",
    "password_confirm": "securepassword123"
  }'
```

#### Success Response (201 Created)
```json
{
  "id": 1,
  "username": "johndoe",
  "email": "john.doe@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "date_joined": "2024-01-15T10:30:00Z"
}
```

#### Validation Rules
- **Username**: Must be unique, alphanumeric with underscores and hyphens only
- **Email**: Must be unique and valid email format
- **Password**: Minimum 8 characters, must meet Django's password strength requirements
- **Password Confirmation**: Must match the password exactly

---

### 2. User Profile Management
**Endpoints:** 
- `GET /api/users/profile/` - Retrieve current user profile
- `PUT /api/users/profile/` - Update entire user profile
- `PATCH /api/users/profile/` - Update partial user profile

Authentication required for all operations.

#### Request Body (PUT/PATCH)
```json
{
  "username": "string (optional)",
  "email": "string (optional)",
  "first_name": "string (optional)",
  "last_name": "string (optional)"
}
```

#### Example Requests

**Get Profile:**
```bash
curl -X GET http://localhost:8000/api/users/profile/ \
  -H "Cookie: sessionid=your_session_id"
```

**Update Profile:**
```bash
curl -X PATCH http://localhost:8000/api/users/profile/ \
  -H "Content-Type: application/json" \
  -H "Cookie: sessionid=your_session_id" \
  -d '{
    "first_name": "John Updated",
    "last_name": "Doe Updated"
  }'
```

#### Success Response (200 OK)
```json
{
  "id": 1,
  "username": "johndoe",
  "email": "john.doe@example.com",
  "first_name": "John Updated",
  "last_name": "Doe Updated",
  "date_joined": "2024-01-15T10:30:00Z"
}
```

#### Read-only Fields
- `id`: Cannot be modified
- `date_joined`: Set automatically on registration

---

### 3. Password Change
**Endpoint:** `POST /api/users/password-change/`

Change the authenticated user's password. Authentication required.

#### Request Body
```json
{
  "old_password": "string (required)",
  "new_password": "string (required, min 8 characters)",
  "new_password_confirm": "string (required, must match new_password)"
}
```

#### Example Request
```bash
curl -X POST http://localhost:8000/api/users/password-change/ \
  -H "Content-Type: application/json" \
  -H "Cookie: sessionid=your_session_id" \
  -d '{
    "old_password": "oldpassword123",
    "new_password": "newsecurepassword456",
    "new_password_confirm": "newsecurepassword456"
  }'
```

#### Success Response (200 OK)
```json
{
  "detail": "Password changed successfully."
}
```

#### Validation Rules
- **Old Password**: Must match current password
- **New Password**: Minimum 8 characters, must meet Django's password strength requirements
- **Password Confirmation**: Must match new password exactly
- **New Password**: Must be different from old password

---

## Error Handling

### Common HTTP Status Codes

| Status Code | Description | Example Scenarios |
|-------------|-------------|-------------------|
| **200 OK** | Success | GET, PUT, PATCH operations |
| **201 Created** | Resource created | User registration |
| **204 No Content** | Success (no response body) | DELETE operations |
| **400 Bad Request** | Invalid request data | Validation errors, missing required fields |
| **401 Unauthorized** | Authentication required | Missing or invalid session |
| **403 Forbidden** | Access denied | Insufficient permissions |
| **404 Not Found** | Resource not found | Invalid endpoint or resource ID |
| **409 Conflict** | Resource conflict | Duplicate username or email |

### Error Response Format

All error responses follow this JSON format:

```json
{
  "field_name": ["Error message 1", "Error message 2"],
  "non_field_errors": ["General error message"]
}
```

#### Example Error Response (400 Bad Request)
```json
{
  "email": ["A user with this email already exists."],
  "password": ["This password is too common."],
  "password_confirm": ["Passwords do not match."]
}
```

#### Example Error Response (401 Unauthorized)
```json
{
  "detail": "Authentication credentials were not provided."
}
```

---

## Frontend Integration Guide

### JavaScript/Fetch API Examples

#### User Registration
```javascript
async function registerUser(userData) {
  try {
    const response = await fetch('http://localhost:8000/api/users/users/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(userData)
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(JSON.stringify(errorData));
    }

    return await response.json();
  } catch (error) {
    console.error('Registration failed:', error);
    throw error;
  }
}

// Usage
const userData = {
  username: 'johndoe',
  email: 'john.doe@example.com',
  first_name: 'John',
  last_name: 'Doe',
  password: 'securepassword123',
  password_confirm: 'securepassword123'
};

registerUser(userData)
  .then(user => console.log('User registered:', user))
  .catch(error => console.error('Error:', error));
```

#### Get User Profile
```javascript
async function getUserProfile(sessionId) {
  try {
    const response = await fetch('http://localhost:8000/api/users/profile/', {
      method: 'GET',
      headers: {
        'Cookie': `sessionid=${sessionId}`
      }
    });

    if (!response.ok) {
      throw new Error('Failed to fetch profile');
    }

    return await response.json();
  } catch (error) {
    console.error('Get profile failed:', error);
    throw error;
  }
}
```

#### Update User Profile
```javascript
async function updateUserProfile(profileData, sessionId) {
  try {
    const response = await fetch('http://localhost:8000/api/users/profile/', {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'Cookie': `sessionid=${sessionId}`
      },
      body: JSON.stringify(profileData)
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(JSON.stringify(errorData));
    }

    return await response.json();
  } catch (error) {
    console.error('Profile update failed:', error);
    throw error;
  }
}
```

#### Password Change
```javascript
async function changePassword(passwordData, sessionId) {
  try {
    const response = await fetch('http://localhost:8000/api/users/password-change/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Cookie': `sessionid=${sessionId}`
      },
      body: JSON.stringify(passwordData)
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(JSON.stringify(errorData));
    }

    return await response.json();
  } catch (error) {
    console.error('Password change failed:', error);
    throw error;
  }
}
```

### React Hook Examples

#### Custom Hook for User Management
```javascript
import { useState, useCallback } from 'react';

const useUserAPI = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const getSessionId = () => {
    // Get session ID from your auth state/storage
    return localStorage.getItem('sessionId');
  };

  const register = useCallback(async (userData) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('http://localhost:8000/api/users/users/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(userData)
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(JSON.stringify(errorData));
      }

      return await response.json();
    } catch (err) {
      const errorMessage = JSON.parse(err.message);
      setError(errorMessage);
      throw errorMessage;
    } finally {
      setLoading(false);
    }
  }, []);

  const getProfile = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('http://localhost:8000/api/users/profile/', {
        method: 'GET',
        headers: { 'Cookie': `sessionid=${getSessionId()}` }
      });

      if (!response.ok) {
        throw new Error('Failed to fetch profile');
      }

      return await response.json();
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const updateProfile = useCallback(async (profileData) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('http://localhost:8000/api/users/profile/', {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'Cookie': `sessionid=${getSessionId()}`
        },
        body: JSON.stringify(profileData)
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(JSON.stringify(errorData));
      }

      return await response.json();
    } catch (err) {
      const errorMessage = JSON.parse(err.message);
      setError(errorMessage);
      throw errorMessage;
    } finally {
      setLoading(false);
    }
  }, []);

  const changePassword = useCallback(async (passwordData) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('http://localhost:8000/api/users/password-change/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Cookie': `sessionid=${getSessionId()}`
        },
        body: JSON.stringify(passwordData)
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(JSON.stringify(errorData));
      }

      return await response.json();
    } catch (err) {
      const errorMessage = JSON.parse(err.message);
      setError(errorMessage);
      throw errorMessage;
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    loading,
    error,
    register,
    getProfile,
    updateProfile,
    changePassword
  };
};

export default useUserAPI;
```

---

## Testing Information

### Testing the Endpoints

#### Using curl

**1. User Registration:**
```bash
curl -X POST http://localhost:8000/api/users/users/ \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "test@example.com",
    "first_name": "Test",
    "last_name": "User",
    "password": "testpassword123",
    "password_confirm": "testpassword123"
  }'
```

**2. Get User Profile (requires authentication):**
```bash
# First, you need to authenticate and get session ID
curl -X GET http://localhost:8000/api/users/profile/ \
  -H "Cookie: sessionid=your_session_id_here"
```

**3. Password Change (requires authentication):**
```bash
curl -X POST http://localhost:8000/api/users/password-change/ \
  -H "Content-Type: application/json" \
  -H "Cookie: sessionid=your_session_id_here" \
  -d '{
    "old_password": "oldpassword",
    "new_password": "newpassword123",
    "new_password_confirm": "newpassword123"
  }'
```

#### Using Postman

1. **Set up Environment:**
   - Create a new environment variable: `base_url = http://localhost:8000`
   - Create a new environment variable: `session_id` (to be set after login)

2. **Test Registration:**
   - Method: `POST`
   - URL: `{{base_url}}/api/users/users/`
   - Headers: `Content-Type: application/json`
   - Body: Raw JSON (use the registration example above)

3. **Test Profile Operations:**
   - Method: `GET`
   - URL: `{{base_url}}/api/users/profile/`
   - Headers: `Cookie: sessionid={{session_id}}`

#### Error Testing

**Test validation errors:**
```bash
# Missing required fields
curl -X POST http://localhost:8000/api/users/users/ \
  -H "Content-Type: application/json" \
  -d '{"username": "test"}'

# Duplicate email
curl -X POST http://localhost:8000/api/users/users/ \
  -H "Content-Type: application/json" \
  -d '{
    "username": "uniqueuser",
    "email": "existing@example.com",
    "password": "password123",
    "password_confirm": "password123"
  }'
```

**Test authentication errors:**
```bash
# Missing session cookie
curl -X GET http://localhost:8000/api/users/profile/

# Invalid session ID
curl -X GET http://localhost:8000/api/users/profile/ \
  -H "Cookie: sessionid=invalid_session_id"
```

### Response Time Expectations

- **User Registration**: 100-500ms
- **Profile Operations**: 50-200ms
- **Password Change**: 100-300ms

### Rate Limiting

Currently, no rate limiting is implemented on these endpoints. However, Django's default security measures are in place.

---

## Best Practices

### Security Considerations

1. **Password Security**: Never log or store passwords in plain text
2. **Session Management**: Implement proper session timeout and secure cookie settings in production
3. **Input Validation**: Always validate input on both client and server side
4. **HTTPS**: Use HTTPS in production to encrypt data in transit

### Error Handling

1. **Graceful Degradation**: Handle API errors gracefully in your frontend
2. **User Feedback**: Provide meaningful error messages to users
3. **Retry Logic**: Implement retry mechanisms for transient failures

### Performance

1. **Caching**: Cache user profile data when appropriate
2. **Batch Operations**: Use PATCH for partial updates instead of PUT
3. **Connection Pooling**: Reuse connections for multiple API calls

---

## Troubleshooting

### Common Issues

**401 Unauthorized:**
- Ensure session cookie is included in requests
- Check if session has expired
- Verify user is properly authenticated

**400 Bad Request:**
- Check request body format and required fields
- Validate JSON syntax
- Ensure field types match expected formats

**500 Internal Server Error:**
- Check server logs for detailed error information
- Verify database connectivity
- Confirm all required services are running

### Getting Help

For additional support or to report issues:
1. Check the Django application logs in the terminal
2. Verify database connectivity
3. Ensure all dependencies are properly installed
4. Check that the Django development server is running on port 8000

---

*This documentation was generated for ToolBox Web Services User Management API. Last updated: October 2024*