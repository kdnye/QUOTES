from __future__ import annotations

"""Authorization helpers for role-based access control."""

from functools import wraps
from typing import Callable, Iterable, Set

from flask import abort, redirect, request, url_for
from flask_login import current_user


def _expand_roles(roles: Iterable[str]) -> Set[str]:
    """Translate high-level role selectors into stored values.

    Args:
        roles: Iterable of role names supplied to :func:`roles_required`.

    Returns:
        Set[str]: Concrete role values saved on :class:`app.models.User`.
    """

    expanded_roles: Set[str] = set()
    for role in roles:
        if role == "employee":
            expanded_roles.update({"employee", "super_admin"})
        elif role == "customer":
            expanded_roles.add("customer")
        elif role == "super_admin":
            expanded_roles.add("super_admin")
        else:
            expanded_roles.add(role)
    return expanded_roles


def roles_required(*roles: str, require_employee_approval: bool = False) -> Callable:
    """Protect a view based on :data:`flask_login.current_user`'s role.

    Unauthenticated users are redirected to the login page. Authenticated users
    must expose a :attr:`app.models.User.role` value contained in ``roles``.
    When ``require_employee_approval`` is set the decorator also validates
    :attr:`app.models.User.employee_approved` for employees. Super admins are
    always allowed and bypass the approval check.

    Args:
        *roles: Acceptable values for :attr:`app.models.User.role`.
        require_employee_approval: When ``True``, ensure that
            :data:`flask_login.current_user` has ``employee_approved`` set when
            their role is ``"employee"``.

    Returns:
        Callable: A decorator enforcing the role restrictions on the wrapped
        view function.
    """

    allowed_roles = _expand_roles(roles)

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.url))

            if getattr(current_user, "is_admin", False):
                return view(*args, **kwargs)

            user_role = getattr(current_user, "role", None)
            if user_role is None:
                abort(403)

            if allowed_roles and user_role not in allowed_roles:
                abort(403)

            if (
                require_employee_approval
                and user_role == "employee"
                and not getattr(current_user, "employee_approved", False)
            ):
                abort(403)

            return view(*args, **kwargs)

        return wrapped

    return decorator


def super_admin_required(view: Callable) -> Callable:
    """Restrict access to ``super_admin`` accounts.

    Args:
        view: View function receiving :data:`flask_login.current_user`.

    Returns:
        Callable: Wrapped view that aborts with ``403`` if the user is not a
        super administrator.
    """

    return roles_required("super_admin")(view)


def employee_required(approved_only: bool = True) -> Callable:
    """Restrict access to employees and super admins.

    Args:
        approved_only: When ``True`` ensure
            :data:`flask_login.current_user` has ``employee_approved`` set.

    Returns:
        Callable: Decorator guaranteeing the wrapped view is only available to
        trusted staff members.
    """

    return roles_required("employee", require_employee_approval=approved_only)


def customer_required(view: Callable) -> Callable:
    """Restrict access to customer accounts.

    Args:
        view: View function that inspects :data:`flask_login.current_user`.

    Returns:
        Callable: Wrapped view that aborts with ``403`` when the user does not
        have a ``customer`` role.
    """

    return roles_required("customer")(view)


def admin_required(view: Callable) -> Callable:
    """Deprecated alias for :func:`super_admin_required`.

    Args:
        view: View protected by :data:`flask_login.current_user` role checks.

    Returns:
        Callable: Output of :func:`super_admin_required` for backwards
        compatibility.
    """

    return super_admin_required(view)
