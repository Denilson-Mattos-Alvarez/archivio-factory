class PrivateResponseMiddleware:
    """Impedisce la conservazione in cache delle risposte dell'archivio privato."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith('/static/'):
            response['Cache-Control'] = 'private, no-store, max-age=0'
            response['Pragma'] = 'no-cache'
            response['Vary'] = 'Cookie, Authorization'
        response['Referrer-Policy'] = 'same-origin'
        response['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response
