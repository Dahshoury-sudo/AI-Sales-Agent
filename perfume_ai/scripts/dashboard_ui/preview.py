"""Template-only preview: no application settings, database or live APIs are loaded."""
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit
import mimetypes
from django.conf import settings
import django

root = Path(__file__).resolve().parents[2]
settings.configure(SECRET_KEY='local-template-preview', DEBUG=True, STATIC_URL='/static/',
    INSTALLED_APPS=['django.contrib.staticfiles'],
    TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates',
        'DIRS':[str(root/'dashboard/templates'),str(root/'products/templates')],
        'APP_DIRS':False, 'OPTIONS':{'loaders':['django.template.loaders.filesystem.Loader']}}])
django.setup()
from django.template.loader import render_to_string

routes = {
    '/dashboard/':'dashboard/store_dashboard.html',
    '/dashboard/login/':'dashboard/login.html',
    '/dashboard/register/':'dashboard/register.html',
    '/dashboard/forgot-password/':'dashboard/forgot_password.html',
    '/dashboard/reset-password/preview/token/':'dashboard/reset_password.html',
    '/api/orders/':'products/orders_dashboard.html',
    '/api/handoff/':'products/handoff_dashboard.html',
    '/api/history/':'products/chat_history.html',
    '/api/demo/':'products/chat.html',
    '/api/import/':'products/bulk_import.html',
}
for template in routes.values(): render_to_string(template)
print('All 10 templates compiled. Preview: http://127.0.0.1:8766/dashboard/',flush=True)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route=urlsplit(self.path).path
        if route in routes:
            content=render_to_string(routes[route]).encode('utf-8'); mime='text/html; charset=utf-8'
        elif route.startswith('/static/dashboard/'):
            directory=(root/'dashboard/static/dashboard').resolve()
            file=(directory/route.removeprefix('/static/dashboard/')).resolve()
            if not file.is_relative_to(directory) or not file.is_file(): self.send_error(404); return
            content=file.read_bytes(); mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        else: self.send_error(404); return
        self.send_response(200); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(content))); self.end_headers(); self.wfile.write(content)
    def log_message(self,*args): pass
ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
