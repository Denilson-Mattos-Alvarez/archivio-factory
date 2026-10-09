"""Accesso Django standard, con limite ai tentativi falliti per indirizzo remoto."""
import hashlib
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpResponse


class AccessoArchivio(LoginView):
    def _cache_key(self):
        # Si usa REMOTE_ADDR, senza fidarsi di header inviati dal client.
        remote = self.request.META.get('REMOTE_ADDR', '')
        return 'accesso-fallito:' + hashlib.sha256(remote.encode()).hexdigest()

    def post(self, request, *args, **kwargs):
        if cache.get(self._cache_key(), 0) >= 10:
            return HttpResponse('Troppi tentativi di accesso. Attendi cinque minuti e riprova.', status=429,
                                headers={'Retry-After': '300'})
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self._cache_key()
        if not cache.add(key, 1, timeout=300):
            try:
                cache.incr(key)
            except ValueError:
                cache.add(key, 1, timeout=300)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._cache_key())
        return super().form_valid(form)
