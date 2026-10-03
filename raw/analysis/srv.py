import http.server, os
TOKEN = os.environ["DL_TOKEN"]; D = "/var/minis/shared/ledgers"
class H(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, p):
        p = p.split("?")[0]
        pre = "/" + TOKEN + "/"
        if not p.startswith(pre):
            return "/nonexistent"
        return os.path.join(D, os.path.basename(p[len(pre):]))
    def list_directory(self, p):
        self.send_error(404); return None
http.server.ThreadingHTTPServer(("", int(os.environ.get("PORT", "8080"))), H).serve_forever()
