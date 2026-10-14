from functools import wraps

from django.core.exceptions import PermissionDenied


def permission_required(permission: str):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.can(permission):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
