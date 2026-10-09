import hashlib

from rest_framework.authentication import BaseAuthentication, SessionAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .models import ChiaveFactory


class SessioneConCsrf(SessionAuthentication):
    """Con una chiave esplicita prevale il suo spazio, dopo il controllo CSRF."""

    def authenticate(self, request):
        sessione = super().authenticate(request)
        header = get_authorization_header(request).split()
        if header and header[0].lower() == b"bearer":
            return None
        return sessione


class FactoryAuthentication(BaseAuthentication):
    """Il token grezzo non è conservato: nel DB rimane soltanto la sua impronta."""

    def authenticate(self, request):
        header = get_authorization_header(request)
        if not header:
            return None
        parti = header.split()
        if not parti:
            raise AuthenticationFailed("Chiave Factory non valida.")
        if parti[0].lower() != b"bearer":
            return None
        if len(parti) != 2 or not parti[1].startswith(b"af_") or len(parti[1]) > 200:
            raise AuthenticationFailed("Chiave Factory non valida.")
        try:
            token = parti[1].decode("ascii")
        except UnicodeDecodeError:
            raise AuthenticationFailed("Chiave Factory non valida.") from None
        impronta = hashlib.sha256(token.encode("ascii")).hexdigest()
        chiave = ChiaveFactory.objects.select_related("user", "spazio").filter(impronta_sha256=impronta, attiva=True, user__is_active=True).first()
        if chiave is None:
            raise AuthenticationFailed("Chiave Factory non valida o revocata.")
        return chiave.user, chiave

    def authenticate_header(self, request):
        return 'Bearer realm="Archivio Factory"'
