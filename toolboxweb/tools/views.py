from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
import time
import json
from .models import ToolCategory, Tool, ToolExecution
from .serializers import ToolCategorySerializer, ToolSerializer, ToolExecutionSerializer


class ToolCategoryViewSet(viewsets.ModelViewSet):
    """ViewSet for managing tool categories"""
    queryset = ToolCategory.objects.all()
    serializer_class = ToolCategorySerializer

    def get_queryset(self):
        queryset = ToolCategory.objects.all()
        # Filter by active categories if specified
        is_active = self.request.query_params.get('active', None)
        if is_active is not None:
            queryset = queryset.filter(tools__is_active=True).distinct()
        return queryset


class ToolViewSet(viewsets.ModelViewSet):
    """ViewSet for managing tools"""
    queryset = Tool.objects.all()
    serializer_class = ToolSerializer

    def get_queryset(self):
        queryset = Tool.objects.all()
        # Filter by category if specified
        category_id = self.request.query_params.get('category', None)
        if category_id is not None:
            queryset = queryset.filter(category_id=category_id)

        # Filter by active status if specified
        is_active = self.request.query_params.get('active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        return queryset


class ToolExecutionViewSet(viewsets.ModelViewSet):
    """ViewSet for managing tool executions"""
    queryset = ToolExecution.objects.all()
    serializer_class = ToolExecutionSerializer

    def get_queryset(self):
        queryset = ToolExecution.objects.all()
        # Filter by tool if specified
        tool_id = self.request.query_params.get('tool', None)
        if tool_id is not None:
            queryset = queryset.filter(tool_id=tool_id)

        # Filter by status if specified
        execution_status = self.request.query_params.get('status', None)
        if execution_status is not None:
            queryset = queryset.filter(status=execution_status)

        return queryset

    def perform_create(self, serializer):
        """Override to automatically set execution time and status"""
        serializer.save()


class ArraySumToolView(APIView):
    """Custom view for array summation functionality"""

    def get(self, request):
        """Handle GET requests for array summation"""
        return self._process_request(request)

    def post(self, request):
        """Handle POST requests for array summation"""
        return self._process_request(request)

    def _process_request(self, request):
        """Process array summation request (shared logic for GET and POST)"""
        try:
            # Handle GET request query parameters
            if request.method == 'GET':
                # Parse query parameters into array format
                values_param = request.query_params.get('values')
                array_param = request.query_params.get('array')

                if array_param:
                    # Handle JSON array string format: ?array=[1,2,3,4,5]
                    try:
                        array_data = json.loads(array_param)
                    except (json.JSONDecodeError, TypeError):
                        return Response(
                            {'error': 'Invalid JSON array format in "array" parameter'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                elif values_param:
                    # Check if this is a single comma-separated value or if there are multiple values parameters
                    values_list = request.query_params.getlist('values')

                    if len(values_list) > 1:
                        # Handle multiple parameters format: ?values=1&values=2&values=3
                        try:
                            array_data = [float(x.strip()) for x in values_list]
                        except (ValueError, TypeError):
                            return Response(
                                {'error': 'Invalid number format in multiple values parameters'},
                                status=status.HTTP_400_BAD_REQUEST
                            )
                    elif ',' in values_param:
                        # Handle comma-separated format: ?values=1,2,3,4,5
                        try:
                            array_data = [float(x.strip()) for x in values_param.split(',')]
                        except (ValueError, TypeError):
                            return Response(
                                {'error': 'Invalid number format in comma-separated values'},
                                status=status.HTTP_400_BAD_REQUEST
                            )
                    else:
                        # Handle single value format: ?values=1
                        try:
                            array_data = [float(values_param)]
                        except (ValueError, TypeError):
                            return Response(
                                {'error': 'Invalid number format in values parameter'},
                                status=status.HTTP_400_BAD_REQUEST
                            )
                else:
                    return Response(
                        {'error': 'Missing required parameters. Use "values" (comma-separated or single) or "array" (JSON string)'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Create input_data structure for consistency with POST
                input_data = {'array': array_data}

            else:
                # Handle POST request (existing logic)
                # Get input data from request
                input_data = request.data.get('input_data', {})

                # Extract array from input data
                array_data = input_data.get('array', [])

            if not isinstance(array_data, list):
                return Response(
                    {'error': 'Input must be an array'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Validate that all elements are numbers
            try:
                numbers = [float(x) for x in array_data]
            except (ValueError, TypeError):
                return Response(
                    {'error': 'All array elements must be numbers'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Calculate sum
            start_time = time.time()
            result = sum(numbers)
            execution_time = time.time() - start_time

            # Create tool execution record
            tool, created = Tool.objects.get_or_create(
                name='Array Sum Tool',
                defaults={
                    'description': 'Calculates the sum of array elements',
                    'category': ToolCategory.objects.get_or_create(
                        name='Math',
                        defaults={'description': 'Mathematical operations and calculations'}
                    )[0],
                    'input_type': 'array',
                    'output_type': 'number'
                }
            )

            # Record the execution
            execution = ToolExecution.objects.create(
                tool=tool,
                input_data=input_data,
                output_data={'sum': result, 'count': len(numbers)},
                execution_time=execution_time,
                status='success'
            )

            return Response({
                'result': result,
                'count': len(numbers),
                'execution_id': execution.id,
                'execution_time': execution_time
            })

        except Exception as e:
            # Record failed execution
            try:
                tool = Tool.objects.get(name='Array Sum Tool')
                ToolExecution.objects.create(
                    tool=tool,
                    input_data=request.data.get('input_data', {}),
                    execution_time=time.time() - time.time(),  # Minimal time for error case
                    status='failed',
                    error_message=str(e)
                )
            except:
                pass  # If tool doesn't exist yet, just continue

            return Response(
                {'error': f'Calculation failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
