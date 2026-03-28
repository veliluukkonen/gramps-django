"""
JWT authentication from query parameter or header.

Media endpoints need to accept JWT via ?jwt= query param
because browsers load images directly (no Authorization header).
"""

from rest_framework_simplejwt.tokens import AccessToken

from apps.auth.models import GrampsUser


def jwt_from_query_or_header(request):
    """
    Authenticate from JWT in Authorization header or ?jwt= query param.

    Returns GrampsUser instance or None.
    """
    # Try Authorization header first
    if request.user and request.user.is_authenticated:
        return request.user

    # Try query parameter
    token_str = request.query_params.get("jwt")
    if not token_str:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token_str = auth_header.split(" ", 1)[1]

    if not token_str:
        return None

    try:
        token = AccessToken(token_str)
        user_id = token.get("sub")
        return GrampsUser.objects.get(pk=user_id)
    except Exception:
        return None
