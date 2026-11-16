import threading
import unittest
from http.server import ThreadingHTTPServer

from tests.mock_apps import JfHandler
from usher.clients import Jellyfin


class JellyfinAppLink(unittest.TestCase):
    def test_link_parts_come_from_jellyfin(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), JfHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            jf = Jellyfin(f"http://127.0.0.1:{srv.server_port}", "b" * 32, "8" * 32)
            self.assertEqual(jf.app_link(), {"server": "e" * 32, "user": "8" * 32})
        finally:
            srv.shutdown()

    def test_no_link_when_jellyfin_is_down(self):
        self.assertIsNone(Jellyfin("http://127.0.0.1:9", "b" * 32, "8" * 32).app_link())


if __name__ == "__main__":
    unittest.main()
