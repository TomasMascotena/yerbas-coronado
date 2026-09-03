from django.db import DatabaseError, connection
from django.http import HttpResponse
from django.views.decorators.http import require_safe


@require_safe
def liveness(request):
    return HttpResponse("ok", content_type="text/plain")


@require_safe
def readiness(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return HttpResponse(
            "unavailable",
            status=503,
            content_type="text/plain",
        )
    return HttpResponse("ok", content_type="text/plain")
